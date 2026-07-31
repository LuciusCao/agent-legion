//! OpenAI-compatible chat completions provider (`gateway`).
//!
//! Speaks SSE streaming (`stream: true` + `stream_options.include_usage`) and
//! aggregates the chunk stream into ONE final assistant message — velites
//! never emits delta events, so the caller only sees the completed message.
//!
//! Dialect tolerances (per the PoC report, docs/architecture/velites-poc-report.md):
//!
//! - Some models behind the gateway answer `application/json` instead of SSE
//!   even when `stream: true` is requested (PoC P2); those responses are
//!   parsed as a plain non-streaming chat completion.
//! - Non-standard SSE lines (`event:`, comments starting with `:`, blank
//!   keep-alive lines) are skipped; only `data:` payloads are interpreted.
//! - Reasoning text arrives as `delta.reasoning_content` (kimi/deepseek
//!   dialect) or `delta.reasoning`; both fold into a `thinking` block.
//! - Cache-read tokens arrive as `usage.prompt_cache_hit_tokens`
//!   (gateway/anthropic-style), `usage.prompt_tokens_details.cached_tokens`
//!   (OpenAI style), or `usage.cached_tokens`; absent → 0.
//!
//! Thinking mapping: `--thinking <level>` is sent as top-level
//! `reasoning_effort` (the OpenAI-compatible shape). The PoC verified the
//! gateway accepts Pi's `--thinking` flag but did not pin down the wire
//! field, so this is the conservative default — validate against the real
//! gateway before flipping to a dialect-specific field.

use std::time::Duration;

use futures_util::StreamExt;
use serde_json::{json, Map, Value};

use super::{CompletionRequest, Provider, ProviderError};
use crate::events::{ContentBlock, Message, Role, StopReason, Usage};

pub struct OpenAiCompatProvider {
    /// Name reported in message metadata (`gateway` / `openai_compat`).
    name: String,
    endpoint: String,
    api_key: String,
    client: reqwest::Client,
}

impl OpenAiCompatProvider {
    pub fn new(
        name: String,
        base_url: String,
        api_key: String,
        timeout: Duration,
    ) -> anyhow::Result<Self> {
        let client = reqwest::Client::builder().timeout(timeout).build()?;
        let base = base_url.trim_end_matches('/');
        let endpoint = if base.ends_with("/chat/completions") {
            base.to_string()
        } else {
            format!("{base}/chat/completions")
        };
        Ok(Self {
            name,
            endpoint,
            api_key,
            client,
        })
    }

    fn build_body(&self, req: &CompletionRequest<'_>) -> Value {
        let mut messages = Vec::new();
        if !req.system.is_empty() {
            messages.push(json!({"role": "system", "content": req.system}));
        }
        for message in req.messages {
            messages.push(wire_message(message));
        }

        let mut body = Map::new();
        body.insert("model".into(), json!(req.model));
        body.insert("messages".into(), Value::Array(messages));
        body.insert("stream".into(), json!(true));
        body.insert("stream_options".into(), json!({"include_usage": true}));
        if !req.tools.is_empty() {
            body.insert(
                "tools".into(),
                Value::Array(req.tools.iter().map(wire_tool).collect()),
            );
        }
        if let Some(thinking) = req.thinking {
            // Conservative default (see module docs): OpenAI-compatible
            // `reasoning_effort`, value passed through (`low`/`medium`/...).
            body.insert("reasoning_effort".into(), json!(thinking));
        }
        Value::Object(body)
    }
}

impl Provider for OpenAiCompatProvider {
    async fn complete(&self, req: &CompletionRequest<'_>) -> Result<Message, ProviderError> {
        // Serialized manually so the reqwest `json` feature stays off
        // (dependency set unchanged since M1).
        let body = serde_json::to_vec(&self.build_body(req))
            .expect("request body serialization cannot fail");
        let response = self
            .client
            .post(&self.endpoint)
            .bearer_auth(&self.api_key)
            .header(reqwest::header::CONTENT_TYPE, "application/json")
            .body(body)
            .send()
            .await
            .map_err(|err| classify_transport_error(&err))?;

        let status = response.status();
        if !status.is_success() {
            let body = response.text().await.unwrap_or_default();
            return Err(classify_http_error(status.as_u16(), &body));
        }

        let is_json = response
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|value| value.to_str().ok())
            .is_some_and(|value| value.contains("application/json"));

        let aggregated = if is_json {
            let body = response
                .text()
                .await
                .map_err(|err| classify_transport_error(&err))?;
            parse_non_streaming(&body)?
        } else {
            read_sse_stream(response).await?
        };

        let stop_reason = match aggregated.finish_reason.as_deref() {
            Some("stop") => StopReason::Stop,
            Some("length") => StopReason::Length,
            Some("tool_calls") => StopReason::ToolUse,
            // A stream that ended without any finish_reason is interrupted:
            // retryable, mirroring Pi's "Stream ended without finish_reason".
            None => {
                return Err(ProviderError::Transient(
                    "stream ended without finish_reason".into(),
                ));
            }
            Some(other) => {
                return Err(ProviderError::Call(format!(
                    "unexpected finish_reason `{other}`"
                )));
            }
        };

        let usage = aggregated.usage.clone();
        let mut message = Message::bare(Role::Assistant, aggregated.into_content());
        message.usage = Some(usage);
        message.provider = Some(self.name.clone());
        message.model = Some(req.model.to_string());
        message.stop_reason = Some(stop_reason);
        Ok(message)
    }
}

/// reqwest send/read failures are transient except body-decode determinism;
/// in practice every transport error here is worth one retry.
fn classify_transport_error(err: &reqwest::Error) -> ProviderError {
    ProviderError::Transient(format!("transport error: {err}"))
}

/// 429 and 5xx are transient; other 4xx (401/403/404, unknown model, bad
/// request) are deterministic and must not be retried.
fn classify_http_error(status: u16, body: &str) -> ProviderError {
    let detail = truncate(body, 500);
    let message = format!("HTTP {status}: {detail}");
    if status == 429 || status >= 500 {
        ProviderError::Transient(message)
    } else {
        ProviderError::Call(message)
    }
}

fn truncate(text: &str, max: usize) -> &str {
    match text.char_indices().nth(max) {
        Some((index, _)) => &text[..index],
        None => text,
    }
}

/// Aggregated state of one completion, built up from SSE chunks (or filled
/// at once from a non-streaming response).
#[derive(Default)]
struct Aggregated {
    text: String,
    thinking: String,
    tool_calls: Vec<ToolCallAcc>,
    usage: Usage,
    finish_reason: Option<String>,
}

#[derive(Default)]
struct ToolCallAcc {
    id: String,
    name: String,
    arguments: String,
}

impl Aggregated {
    fn into_content(self) -> Vec<ContentBlock> {
        let mut content = Vec::new();
        if !self.thinking.is_empty() {
            content.push(ContentBlock::Thinking {
                thinking: self.thinking,
            });
        }
        if !self.text.is_empty() {
            content.push(ContentBlock::Text { text: self.text });
        }
        for (index, call) in self.tool_calls.into_iter().enumerate() {
            let id = if call.id.is_empty() {
                format!("call-{index}")
            } else {
                call.id
            };
            // Arguments stream as a JSON string; malformed/truncated payloads
            // are preserved raw instead of failing the completion.
            let arguments = serde_json::from_str(&call.arguments)
                .unwrap_or_else(|_| Value::String(call.arguments.clone()));
            content.push(ContentBlock::ToolCall {
                id,
                name: call.name,
                arguments,
            });
        }
        if content.is_empty() {
            content.push(ContentBlock::Text {
                text: String::new(),
            });
        }
        content
    }

    fn apply_delta(&mut self, delta: &Value) {
        if let Some(text) = delta.get("content").and_then(Value::as_str) {
            self.text.push_str(text);
        }
        let reasoning = delta
            .get("reasoning_content")
            .or_else(|| delta.get("reasoning"))
            .and_then(Value::as_str);
        if let Some(thinking) = reasoning {
            self.thinking.push_str(thinking);
        }
        if let Some(calls) = delta.get("tool_calls").and_then(Value::as_array) {
            for call in calls {
                let index = call.get("index").and_then(Value::as_u64).unwrap_or(0) as usize;
                if self.tool_calls.len() <= index {
                    self.tool_calls.resize_with(index + 1, ToolCallAcc::default);
                }
                let acc = &mut self.tool_calls[index];
                if let Some(id) = call.get("id").and_then(Value::as_str) {
                    acc.id.push_str(id);
                }
                if let Some(function) = call.get("function") {
                    if let Some(name) = function.get("name").and_then(Value::as_str) {
                        acc.name.push_str(name);
                    }
                    if let Some(arguments) = function.get("arguments").and_then(Value::as_str) {
                        acc.arguments.push_str(arguments);
                    }
                }
            }
        }
    }

    fn apply_chunk(&mut self, chunk: &Value) -> Result<(), ProviderError> {
        if let Some(detail) = extract_error_detail(chunk) {
            return Err(ProviderError::Call(format!("stream error: {detail}")));
        }
        if let Some(usage) = chunk.get("usage") {
            self.usage = parse_usage(usage);
        }
        if let Some(choice) = chunk
            .get("choices")
            .and_then(Value::as_array)
            .and_then(|choices| choices.first())
        {
            if let Some(delta) = choice.get("delta") {
                self.apply_delta(delta);
            }
            if let Some(finish) = choice.get("finish_reason").and_then(Value::as_str) {
                self.finish_reason = Some(finish.to_string());
            }
        }
        Ok(())
    }
}

/// Extract an upstream error detail from a response/chunk body. Recognizes
/// the OpenAI shape `{"error": {"message": ...}}` and the gateway's
/// non-standard `{"code": <non-zero>, "msg": ...}` (sqai returns HTTP 200
/// with this body for dead models, e.g. doubao-seed-2.1-turbo-2).
fn extract_error_detail(value: &Value) -> Option<String> {
    if let Some(error) = value.get("error") {
        let detail = error
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("unknown error");
        return Some(detail.to_string());
    }
    let code = value.get("code").and_then(Value::as_i64).unwrap_or(0);
    if code != 0 {
        if let Some(msg) = value.get("msg").and_then(Value::as_str) {
            return Some(format!("code={code}: {msg}"));
        }
    }
    None
}

/// usage.input ← prompt_tokens, usage.output ← completion_tokens,
/// usage.cacheRead ← prompt_cache_hit_tokens (gateway) ‖
/// prompt_tokens_details.cached_tokens (OpenAI) ‖ cached_tokens; 0 if absent.
fn parse_usage(usage: &Value) -> Usage {
    let get = |key: &str| usage.get(key).and_then(Value::as_u64).unwrap_or(0);
    let cache_read = get("prompt_cache_hit_tokens")
        .max(get("cached_tokens"))
        .max(
            usage
                .get("prompt_tokens_details")
                .and_then(|details| details.get("cached_tokens"))
                .and_then(Value::as_u64)
                .unwrap_or(0),
        );
    Usage {
        input: get("prompt_tokens"),
        output: get("completion_tokens"),
        cache_read,
    }
}

/// Parse a non-streaming chat completion body (the dialect some gateway
/// models answer with even when `stream: true` was requested — PoC P2).
fn parse_non_streaming(body: &str) -> Result<Aggregated, ProviderError> {
    let value: Value = serde_json::from_str(body)
        .map_err(|err| ProviderError::Call(format!("invalid JSON response: {err}")))?;
    if let Some(detail) = extract_error_detail(&value) {
        return Err(ProviderError::Call(format!(
            "HTTP 200 error body: {detail}"
        )));
    }
    let mut aggregated = Aggregated::default();
    if let Some(usage) = value.get("usage") {
        aggregated.usage = parse_usage(usage);
    }
    let choice = value
        .get("choices")
        .and_then(Value::as_array)
        .and_then(|choices| choices.first())
        .ok_or_else(|| ProviderError::Call("response has no choices".into()))?;
    if let Some(message) = choice.get("message") {
        if let Some(text) = message.get("content").and_then(Value::as_str) {
            aggregated.text.push_str(text);
        }
        let reasoning = message
            .get("reasoning_content")
            .or_else(|| message.get("reasoning"))
            .and_then(Value::as_str);
        if let Some(thinking) = reasoning {
            aggregated.thinking.push_str(thinking);
        }
        // Non-streaming tool_calls are complete objects without a per-chunk
        // `index`; assign by position.
        if let Some(calls) = message.get("tool_calls").and_then(Value::as_array) {
            for call in calls {
                let mut acc = ToolCallAcc::default();
                if let Some(id) = call.get("id").and_then(Value::as_str) {
                    acc.id = id.to_string();
                }
                if let Some(function) = call.get("function") {
                    if let Some(name) = function.get("name").and_then(Value::as_str) {
                        acc.name = name.to_string();
                    }
                    if let Some(arguments) = function.get("arguments").and_then(Value::as_str) {
                        acc.arguments = arguments.to_string();
                    }
                }
                aggregated.tool_calls.push(acc);
            }
        }
    }
    if let Some(finish) = choice.get("finish_reason").and_then(Value::as_str) {
        aggregated.finish_reason = Some(finish.to_string());
    }
    Ok(aggregated)
}

/// Read an SSE response body to completion, folding every `data:` payload
/// into the aggregate. Blank lines, comments (`:`), and non-data fields
/// (`event:`, `id:`, `retry:`) are skipped.
async fn read_sse_stream(response: reqwest::Response) -> Result<Aggregated, ProviderError> {
    let mut aggregated = Aggregated::default();
    let mut buffer = String::new();
    let mut stream = response.bytes_stream();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|err| classify_transport_error(&err))?;
        buffer.push_str(&String::from_utf8_lossy(&chunk));
        while let Some(pos) = buffer.find('\n') {
            let line = buffer[..pos].to_string();
            buffer.drain(..=pos);
            apply_sse_line(&mut aggregated, &line)?;
        }
    }
    // Flush a trailing line without a newline terminator.
    if !buffer.is_empty() {
        let rest = std::mem::take(&mut buffer);
        apply_sse_line(&mut aggregated, &rest)?;
    }
    Ok(aggregated)
}

fn apply_sse_line(aggregated: &mut Aggregated, line: &str) -> Result<(), ProviderError> {
    let line = line.trim_end_matches('\r');
    if line.is_empty() || line.starts_with(':') {
        return Ok(());
    }
    let Some(payload) = line.strip_prefix("data:") else {
        // event: / id: / retry: and any non-standard field — ignored.
        return Ok(());
    };
    let payload = payload.trim_start();
    if payload.is_empty() || payload == "[DONE]" {
        return Ok(());
    }
    let chunk: Value = serde_json::from_str(payload)
        .map_err(|err| ProviderError::Call(format!("invalid SSE data JSON: {err}")))?;
    aggregated.apply_chunk(&chunk)
}

/// Convert a velites message into the OpenAI wire shape.
fn wire_message(message: &Message) -> Value {
    match message.role {
        Role::User => json!({
            "role": "user",
            "content": joined_text(message),
        }),
        Role::Assistant => {
            let mut wire = Map::new();
            wire.insert("role".into(), json!("assistant"));
            let text = joined_text(message);
            if text.is_empty() {
                wire.insert("content".into(), Value::Null);
            } else {
                wire.insert("content".into(), json!(text));
            }
            // Thinking blocks are not sent back: OpenAI-compatible endpoints
            // treat reasoning as ephemeral per-turn state.
            let tool_calls: Vec<Value> = message
                .content
                .iter()
                .filter_map(|block| match block {
                    ContentBlock::ToolCall {
                        id,
                        name,
                        arguments,
                    } => Some(json!({
                        "id": id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": arguments.to_string(),
                        },
                    })),
                    _ => None,
                })
                .collect();
            if !tool_calls.is_empty() {
                wire.insert("tool_calls".into(), Value::Array(tool_calls));
            }
            Value::Object(wire)
        }
        Role::ToolResult => json!({
            "role": "tool",
            "tool_call_id": message.tool_call_id.as_deref().unwrap_or_default(),
            "content": joined_text(message),
        }),
    }
}

fn joined_text(message: &Message) -> String {
    message
        .content
        .iter()
        .filter_map(|block| match block {
            ContentBlock::Text { text } => Some(text.as_str()),
            _ => None,
        })
        .collect::<Vec<_>>()
        .join("\n")
}

fn wire_tool(spec: &super::ToolSpec) -> Value {
    json!({
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extract_error_detail_openai_shape() {
        let value = json!({"error": {"message": "bad api key"}});
        assert_eq!(extract_error_detail(&value).as_deref(), Some("bad api key"));
        assert_eq!(extract_error_detail(&json!({"choices": []})), None);
    }

    #[test]
    fn extract_error_detail_gateway_code_msg_shape() {
        // sqai gateway answers HTTP 200 with this body for dead models.
        let value = json!({"code": 1, "msg": "model error.", "data": {}});
        assert_eq!(
            extract_error_detail(&value).as_deref(),
            Some("code=1: model error.")
        );
        // code=0 is success, not an error.
        assert_eq!(extract_error_detail(&json!({"code": 0, "msg": "ok"})), None);
    }

    #[test]
    fn non_streaming_gateway_error_body_surfaced() {
        let err = parse_non_streaming(r#"{"code":1,"msg":"model error.","data":{}}"#)
            .err()
            .expect("gateway error body must fail");
        assert!(err.to_string().contains("model error."));
    }

    #[test]
    fn parse_usage_prefers_gateway_cache_field() {
        let usage = parse_usage(&json!({
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_cache_hit_tokens": 42,
            "prompt_tokens_details": {"cached_tokens": 7},
        }));
        assert_eq!(usage.input, 100);
        assert_eq!(usage.output, 20);
        assert_eq!(usage.cache_read, 42);
    }

    #[test]
    fn parse_usage_openai_style_cache_field() {
        let usage = parse_usage(&json!({
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 3},
        }));
        assert_eq!(usage.cache_read, 3);
        assert_eq!(parse_usage(&json!({})).cache_read, 0);
    }

    #[test]
    fn delta_aggregation_text_thinking_tools() {
        let mut aggregated = Aggregated::default();
        aggregated
            .apply_chunk(&json!({
                "choices": [{"delta": {"reasoning_content": "hmm "}}]
            }))
            .unwrap();
        aggregated
            .apply_chunk(&json!({
                "choices": [{"delta": {"reasoning": "yes", "content": "Hel"}}]
            }))
            .unwrap();
        aggregated
            .apply_chunk(&json!({
                "choices": [{"delta": {"content": "lo", "tool_calls": [
                    {"index": 0, "id": "call_1", "function": {"name": "read", "arguments": "{\"pa"}}
                ]}}]
            }))
            .unwrap();
        aggregated
            .apply_chunk(&json!({
                "choices": [{"delta": {"tool_calls": [
                    {"index": 0, "function": {"arguments": "th\":\"a\"}"}}
                ]}, "finish_reason": "tool_calls"}]
            }))
            .unwrap();
        aggregated
            .apply_chunk(&json!({
                "choices": [],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2}
            }))
            .unwrap();

        assert_eq!(aggregated.thinking, "hmm yes");
        assert_eq!(aggregated.text, "Hello");
        assert_eq!(aggregated.finish_reason.as_deref(), Some("tool_calls"));
        let content = aggregated.into_content();
        assert_eq!(content.len(), 3);
        assert!(
            matches!(&content[0], ContentBlock::Thinking { thinking } if thinking == "hmm yes")
        );
        assert!(matches!(&content[1], ContentBlock::Text { text } if text == "Hello"));
        match &content[2] {
            ContentBlock::ToolCall {
                id,
                name,
                arguments,
            } => {
                assert_eq!(id, "call_1");
                assert_eq!(name, "read");
                assert_eq!(arguments, &json!({"path": "a"}));
            }
            other => panic!("expected toolCall, got {other:?}"),
        }
    }

    #[test]
    fn non_streaming_parse_full_response() {
        let body = json!({
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "done",
                    "reasoning_content": "thought",
                    "tool_calls": [{
                        "id": "c1", "type": "function",
                        "function": {"name": "write", "arguments": "{\"path\":\"x\"}"}
                    }]
                },
                "finish_reason": "tool_calls"
            }],
            "usage": {"prompt_tokens": 9, "completion_tokens": 4, "cached_tokens": 1}
        })
        .to_string();
        let aggregated = parse_non_streaming(&body).unwrap();
        assert_eq!(aggregated.text, "done");
        assert_eq!(aggregated.thinking, "thought");
        assert_eq!(aggregated.usage.cache_read, 1);
        assert_eq!(aggregated.tool_calls[0].name, "write");
        assert_eq!(aggregated.tool_calls[0].arguments, "{\"path\":\"x\"}");
    }

    #[test]
    fn http_error_classification() {
        assert!(classify_http_error(429, "slow down").is_retryable());
        assert!(classify_http_error(500, "boom").is_retryable());
        assert!(classify_http_error(503, "boom").is_retryable());
        assert!(!classify_http_error(401, "no").is_retryable());
        assert!(!classify_http_error(404, "model not found").is_retryable());
        assert!(!classify_http_error(400, "bad request").is_retryable());
    }

    #[test]
    fn sse_line_tolerates_dialect_noise() {
        let mut aggregated = Aggregated::default();
        for line in [
            ": keep-alive",
            "",
            "event: message",
            "id: 7",
            "retry: 3000",
            "data: {\"choices\":[{\"delta\":{\"content\":\"hi\"}}]}",
            "data:{\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}]}",
            "data: [DONE]",
        ] {
            apply_sse_line(&mut aggregated, line).unwrap();
        }
        assert_eq!(aggregated.text, "hi");
        assert_eq!(aggregated.finish_reason.as_deref(), Some("stop"));
    }

    #[test]
    fn truncate_is_char_boundary_safe() {
        assert_eq!(truncate("héllo", 2), "hé");
        assert_eq!(truncate("héllo", 3), "hél");
        assert_eq!(truncate("hi", 500), "hi");
    }
}
