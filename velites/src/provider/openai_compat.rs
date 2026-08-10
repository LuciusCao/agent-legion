//! OpenAI-compatible chat completions provider (`gateway`).
//!
//! Speaks SSE streaming (`stream: true` + `stream_options.include_usage`) and
//! aggregates the chunk stream into ONE final assistant message — velites
//! never emits delta events, so the caller only sees the completed message.
//!
//! Timeout structure (design §7): connect timeout bounds connection setup
//! only; a per-read idle timeout bounds silence BETWEEN chunks; there is
//! deliberately NO whole-request timeout — long generations stream for many
//! minutes and the run-level wall-clock budget is the agent loop's deadline
//! (`--timeout-seconds`, design §5), not the HTTP layer's.
//!
//! Dialect tolerances (per the PoC report, docs/architecture/velites-poc-report.md):
//!
//! - Some models behind the gateway answer `application/json` instead of SSE
//!   even when `stream: true` is requested (PoC P2); those responses are
//!   parsed as a plain non-streaming chat completion.
//! - Non-standard SSE lines (`event:`, comments starting with `:`, blank
//!   keep-alive lines) are skipped; only `data:` payloads are interpreted.
//! - A `data:` payload that fails JSON parsing is treated as TRANSIENT
//!   stream corruption (proxy/gateway mangling), never as a deterministic
//!   call failure. Same for a `tool_calls` delta whose `index` exceeds
//!   [`MAX_TOOL_CALL_INDEX`] — absurd indexes are corruption, and the
//!   accumulator must never resize to a gateway-controlled length.
//! - Chunk boundaries may split multi-byte UTF-8 sequences; lines are
//!   assembled from raw bytes and decoded only once complete.
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

use std::time::{Duration, Instant};

use futures_util::StreamExt;
use serde_json::{json, Map, Value};

use super::{CompletionRequest, Provider, ProviderError};
use crate::events::{ContentBlock, Message, RequestTiming, Role, StopReason, Usage};

/// Bounds connection setup; streaming itself has no total deadline.
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
/// Bounds silence between SSE chunks. Generous on purpose: reasoning models
/// can pause between chunks, and the gateway kills dead streams well before
/// this fires (observed ~52s). The run wall-clock budget caps total time.
const DEFAULT_READ_IDLE_TIMEOUT: Duration = Duration::from_secs(180);

pub struct OpenAiCompatProvider {
    /// Name reported in message metadata (`gateway` / `openai_compat`).
    name: String,
    endpoint: String,
    api_key: String,
    client: reqwest::Client,
    read_idle_timeout: Duration,
}

impl OpenAiCompatProvider {
    pub fn new(name: String, base_url: String, api_key: String) -> anyhow::Result<Self> {
        let client = reqwest::Client::builder()
            .connect_timeout(CONNECT_TIMEOUT)
            .build()?;
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
            read_idle_timeout: DEFAULT_READ_IDLE_TIMEOUT,
        })
    }

    /// Override the per-read idle timeout (tests).
    pub fn with_read_idle_timeout(mut self, timeout: Duration) -> Self {
        self.read_idle_timeout = timeout;
        self
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
        // Request-level timing starts at the POST, before connect+TLS.
        let started = Instant::now();
        let response = self
            .client
            .post(&self.endpoint)
            .bearer_auth(&self.api_key)
            .header(reqwest::header::CONTENT_TYPE, "application/json")
            .body(body)
            .send()
            .await
            .map_err(|err| classify_transport_error(&err))?;
        // reqwest resolves `send()` once response headers arrive.
        let headers_at = Instant::now();

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

        let (aggregated, first_chunk_at) = if is_json {
            // Non-streaming fallback (PoC P2): the whole body is one "chunk",
            // so headers double as the first-byte mark.
            let body = response
                .text()
                .await
                .map_err(|err| classify_transport_error(&err))?;
            (parse_non_streaming(&body)?, Some(headers_at))
        } else {
            read_sse_stream(response, self.read_idle_timeout).await?
        };
        let ended = Instant::now();

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
        // Timing is attached only here, on the success path: every error
        // branch above returns early, and retried attempts surface as
        // pi-compatible error events without timing (see retry_attempt_events).
        let first_chunk_at = first_chunk_at.unwrap_or(headers_at);
        message.timing = Some(RequestTiming {
            ttfb_ms: millis(first_chunk_at.saturating_duration_since(started)),
            stream_ms: millis(ended.saturating_duration_since(first_chunk_at)),
            total_ms: millis(ended.saturating_duration_since(started)),
        });
        Ok(message)
    }
}

fn millis(duration: Duration) -> u64 {
    u64::try_from(duration.as_millis()).unwrap_or(u64::MAX)
}

/// reqwest send/read failures are transient (connection reset, mid-stream
/// close, timeouts): retrying the identical request is safe, and the Host's
/// model-error folding treats mid-connection failure as transient too.
/// reqwest's top-level Display flattens everything into "error decoding
/// response body", so walk the source chain to expose the actionable root
/// cause (hyper IncompleteMessage, OS reset, ...).
fn classify_transport_error(err: &reqwest::Error) -> ProviderError {
    let kind = if err.is_connect() {
        "connect"
    } else if err.is_timeout() {
        "timeout"
    } else {
        "transport"
    };
    ProviderError::Transient(format!("{kind} error: {err} ({})", source_chain(err)))
}

/// Render the std::error::Error source chain below the top-level error,
/// deduplicated against itself (some wrappers repeat the same message).
fn source_chain(err: &dyn std::error::Error) -> String {
    let mut causes: Vec<String> = Vec::new();
    let mut source = err.source();
    while let Some(cause) = source {
        let text = cause.to_string();
        if causes.last() != Some(&text) {
            causes.push(text);
        }
        source = cause.source();
    }
    if causes.is_empty() {
        "no underlying cause".to_string()
    } else {
        causes.join(" <== ")
    }
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

/// Upper bound on a streamed `tool_calls[].index`. Real models emit a
/// handful of sequentially numbered calls; anything beyond this is stream
/// corruption (a mangled proxy chunk), rejected before `resize_with` could
/// turn it into an overflow/OOM panic.
const MAX_TOOL_CALL_INDEX: u64 = 1024;

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

    fn apply_delta(&mut self, delta: &Value) -> Result<(), ProviderError> {
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
                let index = call.get("index").and_then(Value::as_u64).unwrap_or(0);
                // A malformed chunk can carry a huge `index`; resizing the
                // accumulator to it would panic (overflow/OOM), killing the
                // run without an `agent_end`. Real models emit a handful of
                // sequentially numbered calls, so anything beyond the cap is
                // stream corruption — transient, like a malformed chunk.
                if index > MAX_TOOL_CALL_INDEX {
                    return Err(ProviderError::Transient(format!(
                        "malformed tool_calls index {index} (stream corruption)"
                    )));
                }
                let index = index as usize;
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
        Ok(())
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
                self.apply_delta(delta)?;
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
/// non-standard `{"code": <non-zero>, "msg": ...}` (the gateway returns
/// HTTP 200 with this body for dead models).
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

/// usage.input ← prompt_tokens − cacheRead (pi semantics: `input` EXCLUDES
/// cache-read tokens — the provider's `prompt_tokens` includes them, and
/// billing counts input and cacheRead separately, so passing prompt_tokens
/// through unchanged would double-bill the cached part; saturates at 0),
/// usage.output ← completion_tokens,
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
        input: get("prompt_tokens").saturating_sub(cache_read),
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

/// Assemble SSE lines from raw byte chunks. Chunk boundaries may split a
/// multi-byte UTF-8 sequence, so decoding happens only once a full line
/// (newline-terminated) has arrived — a valid UTF-8 line never contains a
/// partial character.
#[derive(Default)]
struct SseLineBuffer {
    buffer: Vec<u8>,
}

impl SseLineBuffer {
    /// Append one TCP chunk; returns every line completed by it.
    fn push(&mut self, chunk: &[u8]) -> Vec<String> {
        self.buffer.extend_from_slice(chunk);
        let mut lines = Vec::new();
        while let Some(pos) = self.buffer.iter().position(|b| *b == b'\n') {
            let line: Vec<u8> = self.buffer.drain(..=pos).collect();
            lines.push(String::from_utf8_lossy(&line[..line.len() - 1]).into_owned());
        }
        lines
    }

    /// Flush a trailing line without a newline terminator, if any.
    fn finish(&mut self) -> Option<String> {
        if self.buffer.is_empty() {
            None
        } else {
            let rest = std::mem::take(&mut self.buffer);
            Some(String::from_utf8_lossy(&rest).into_owned())
        }
    }
}

/// Read an SSE response body to completion, folding every `data:` payload
/// into the aggregate. Blank lines, comments (`:`), and non-data fields
/// (`event:`, `id:`, `retry:`) are skipped. Each read is bounded by
/// `idle_timeout`: total silence longer than that means a dead stream.
///
/// Returns the aggregate plus the instant the first `data:` payload arrived
/// (None when the stream carried none), feeding request-level timing.
async fn read_sse_stream(
    response: reqwest::Response,
    idle_timeout: Duration,
) -> Result<(Aggregated, Option<Instant>), ProviderError> {
    let mut aggregated = Aggregated::default();
    let mut lines = SseLineBuffer::default();
    let mut first_chunk_at: Option<Instant> = None;
    let mut stream = response.bytes_stream();
    loop {
        let next = match tokio::time::timeout(idle_timeout, stream.next()).await {
            Ok(next) => next,
            Err(_) => {
                return Err(ProviderError::Transient(format!(
                    "stream idle for over {}s",
                    idle_timeout.as_secs()
                )));
            }
        };
        let Some(chunk) = next else { break };
        let chunk = chunk.map_err(|err| classify_transport_error(&err))?;
        for line in lines.push(&chunk) {
            if apply_sse_line(&mut aggregated, &line)? && first_chunk_at.is_none() {
                first_chunk_at = Some(Instant::now());
            }
        }
    }
    if let Some(rest) = lines.finish() {
        if apply_sse_line(&mut aggregated, &rest)? && first_chunk_at.is_none() {
            first_chunk_at = Some(Instant::now());
        }
    }
    Ok((aggregated, first_chunk_at))
}

/// Fold one SSE line into the aggregate. Returns `Ok(true)` when the line was
/// a JSON `data:` payload (the timing layer counts the first one as TTFB);
/// blank lines, comments, other fields, and `data: [DONE]` are `Ok(false)`.
fn apply_sse_line(aggregated: &mut Aggregated, line: &str) -> Result<bool, ProviderError> {
    let line = line.trim_end_matches('\r');
    if line.is_empty() || line.starts_with(':') {
        return Ok(false);
    }
    let Some(payload) = line.strip_prefix("data:") else {
        // event: / id: / retry: and any non-standard field — ignored.
        return Ok(false);
    };
    let payload = payload.trim_start();
    if payload.is_empty() || payload == "[DONE]" {
        return Ok(false);
    }
    // A data payload that fails JSON parsing mid-stream is proxy/gateway
    // corruption (chunked-encoding mangling, truncation), not a model
    // contract violation — transient, so the retry layer re-asks cleanly.
    let chunk: Value = serde_json::from_str(payload).map_err(|err| {
        ProviderError::Transient(format!(
            "malformed SSE data chunk: {err}: {}",
            truncate(payload, 200)
        ))
    })?;
    aggregated.apply_chunk(&chunk)?;
    Ok(true)
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
            if text.is_empty() {
                // `content: null` is only safe alongside tool_calls: the
                // gateway instantly kills the SSE stream (HTTP 200, then a
                // mid-chunk connection close) when a tool-call-less assistant
                // message carries null content — verified 2026-08-01 against
                // the production gateway. A thinking-only assistant message
                // (thinking blocks are not sent back) must degrade to "".
                if tool_calls.is_empty() {
                    wire.insert("content".into(), json!(""));
                } else {
                    wire.insert("content".into(), Value::Null);
                }
            } else {
                wire.insert("content".into(), json!(text));
            }
            // Thinking blocks are not sent back: OpenAI-compatible endpoints
            // treat reasoning as ephemeral per-turn state.
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
        // The gateway answers HTTP 200 with this body for dead models.
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
        // pi 口径: input excludes the cached part (100 − 42).
        assert_eq!(usage.input, 58);
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
        assert_eq!(usage.input, 7);
        assert_eq!(usage.cache_read, 3);
        assert_eq!(parse_usage(&json!({})).cache_read, 0);
    }

    #[test]
    fn parse_usage_input_saturates_when_cache_exceeds_prompt_tokens() {
        // Defensive: a misbehaving gateway must not underflow the counter.
        let usage = parse_usage(&json!({
            "prompt_tokens": 5,
            "completion_tokens": 1,
            "prompt_cache_hit_tokens": 9,
        }));
        assert_eq!(usage.input, 0);
        assert_eq!(usage.cache_read, 9);
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
    fn absurd_tool_call_index_is_transient_corruption_not_panic() {
        // Regression: a malformed chunk with a huge `index` must not reach
        // `resize_with` — `index + 1` overflow / OOM would kill the process
        // without an `agent_end`. Corruption is transient, like a malformed
        // JSON chunk.
        let mut aggregated = Aggregated::default();
        let err = aggregated
            .apply_chunk(&json!({
                "choices": [{"delta": {"tool_calls": [
                    {"index": u64::MAX, "function": {"name": "read", "arguments": "{}"}}
                ]}}]
            }))
            .expect_err("absurd index must be rejected");
        assert!(err.is_retryable(), "stream corruption is transient: {err}");

        // Beyond the cap without overflowing u64 is equally malformed.
        let mut aggregated = Aggregated::default();
        assert!(
            aggregated
                .apply_chunk(&json!({
                    "choices": [{"delta": {"tool_calls": [{"index": 100_000}]}}]
                }))
                .is_err(),
            "index beyond the cap must be rejected"
        );

        // Sequential indexes still aggregate normally.
        let mut aggregated = Aggregated::default();
        aggregated
            .apply_chunk(&json!({
                "choices": [{"delta": {"tool_calls": [
                    {"index": 0, "function": {"name": "read", "arguments": ""}},
                    {"index": 1, "function": {"name": "write", "arguments": ""}}
                ]}}]
            }))
            .unwrap();
        assert_eq!(aggregated.tool_calls.len(), 2);
        assert_eq!(aggregated.tool_calls[1].name, "write");
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
        assert_eq!(aggregated.usage.input, 8);
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
    fn sse_line_buffer_keeps_multibyte_utf8_intact_across_chunks() {
        // A Chinese character split across two TCP chunks must not become
        // U+FFFD replacement garbage (production streams are Chinese-heavy).
        let line = "data: {\"choices\":[{\"delta\":{\"content\":\"你好\"}}]}\n";
        let bytes = line.as_bytes();
        // Split inside the multi-byte encoding of 你 (E4 BD A0).
        let split = bytes
            .windows(1)
            .position(|w| w == b"\xBD")
            .expect("test line must contain a split point");
        let mut buffer = SseLineBuffer::default();
        assert!(
            buffer.push(&bytes[..split]).is_empty(),
            "no complete line yet"
        );
        let lines = buffer.push(&bytes[split..]);
        assert_eq!(lines.len(), 1);
        assert!(lines[0].contains("你好"), "got: {}", lines[0]);
        assert!(buffer.finish().is_none());
    }

    #[test]
    fn sse_line_buffer_flushes_unterminated_tail() {
        let mut buffer = SseLineBuffer::default();
        let lines = buffer.push(b"data: [DONE]\ndata: tail");
        assert_eq!(lines, vec!["data: [DONE]".to_string()]);
        let rest = buffer.finish().expect("tail must flush");
        assert_eq!(rest, "data: tail");
    }

    #[test]
    fn malformed_sse_data_chunk_is_transient() {
        let mut aggregated = Aggregated::default();
        let err = apply_sse_line(&mut aggregated, "data: {\"choices\":[{\"delta\":")
            .expect_err("malformed JSON must fail");
        assert!(err.is_retryable(), "stream corruption is transient: {err}");
        assert!(
            err.to_string().contains("malformed SSE data chunk"),
            "got: {err}"
        );
    }

    #[test]
    fn truncate_is_char_boundary_safe() {
        assert_eq!(truncate("héllo", 2), "hé");
        assert_eq!(truncate("héllo", 3), "hél");
        assert_eq!(truncate("hi", 500), "hi");
    }

    #[test]
    fn wire_assistant_without_text_degrades_to_empty_string_not_null() {
        // A thinking-only assistant message must NOT serialize as
        // `content: null` — the gateway kills the SSE stream for
        // tool-call-less null content (verified against production gateway).
        let thinking_only = Message::bare(
            Role::Assistant,
            vec![ContentBlock::Thinking {
                thinking: "hmm".into(),
            }],
        );
        let wire = wire_message(&thinking_only);
        assert_eq!(wire["content"], json!(""));
        assert!(wire.get("tool_calls").is_none());

        // With tool calls, null content stays (OpenAI-conventional and
        // accepted by the gateway).
        let with_call = Message::bare(
            Role::Assistant,
            vec![
                ContentBlock::Thinking {
                    thinking: "hmm".into(),
                },
                ContentBlock::ToolCall {
                    id: "c1".into(),
                    name: "read".into(),
                    arguments: json!({"path": "a"}),
                },
            ],
        );
        let wire = wire_message(&with_call);
        assert_eq!(wire["content"], Value::Null);
        assert_eq!(wire["tool_calls"][0]["id"], json!("c1"));
    }
}
