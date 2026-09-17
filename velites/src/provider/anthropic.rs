//! Anthropic Messages API provider with streaming tool-use support.
//!
//! #637 read-side caps (mirroring [`super::openai_compat`], see the review
//! of that module's compound-storm fix): `max_tokens` in the request body
//! only constrains a WELL-BEHAVED server — an anomalous or hostile
//! gateway/proxy stream ignores it, so the client-side caps below are the
//! only bound on such a stream:
//!
//! - [`MAX_SSE_LINE_BYTES`]: an unterminated SSE line longer than any
//!   legitimate `data:` payload is stream corruption, rejected at the line
//!   buffer instead of growing it without bound (a junk flood with no
//!   newline never reaches the aggregate caps).
//! - [`MAX_STREAMED_FIELD_CHARS`]: one aggregated field (text/partial_json/
//!   signature/...) may not exceed 32 MiB.
//! - [`MAX_AGGREGATE_CHARS`]: the WHOLE aggregate (all blocks, all fields)
//!   may not exceed 32 MiB — 1025 blocks × per-field-capped sizes would
//!   otherwise be a compound storm the per-field cap alone cannot see.

use std::collections::BTreeMap;
use std::time::{Duration, Instant};

use futures_util::StreamExt;
use serde_json::{json, Map, Value};

use super::{CompletionRequest, Provider, ProviderError};
use crate::events::{ContentBlock, Message, RequestTiming, Role, StopReason, Usage};

const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const READ_IDLE_TIMEOUT: Duration = Duration::from_secs(180);
const DEFAULT_MAX_OUTPUT_TOKENS: u64 = 8192;

/// 单条未完成 SSE 行（一个 `data:` 载荷）至多几 KB。超过该长度的行是
/// 流损坏，行缓冲必须拒绝而不是无界保留（#637：无换行的 junk 流永远
/// 到不了聚合上限，只能在这里拦）。与 openai_compat 的同名常量同值。
const MAX_SSE_LINE_BYTES: usize = 2 * 1024 * 1024;

/// 单个聚合字段（text / partial_json / signature / ...）的防御性上限
/// （#637）：32 MiB ≈ 800 万 token 的输出，正常路径下远够不到；只拦
/// 网关/代理侧的异常流（delta 泛洪）。与 openai_compat 的
/// MAX_STREAMED_TEXT_CHARS 同值。
const MAX_STREAMED_FIELD_CHARS: usize = 32 * 1024 * 1024;

/// 整个补全聚合的全局上限（#637）：Anthropic 的 `ensure` 允许 0..=1024
/// 共 1025 个 content block，每个 block 又有多个聚合字段——全部单独
/// 在 per-field 上限内时，聚合总量仍可达几十 GiB（#637 的复合风暴面）。
/// 全局检查设在 `apply` 尾部（每条事件后的单一增长咽喉点）。与
/// openai_compat 的 MAX_AGGREGATE_CHARS 同值。
const MAX_AGGREGATE_CHARS: usize = 32 * 1024 * 1024;

pub struct AnthropicProvider {
    name: String,
    endpoint: String,
    api_key: String,
    version: String,
    max_output_tokens: u64,
    thinking_budgets: BTreeMap<String, u64>,
    client: reqwest::Client,
    read_idle_timeout: Duration,
}

impl AnthropicProvider {
    pub fn new(
        name: String,
        base_url: String,
        api_key: String,
        version: String,
        max_output_tokens: Option<u64>,
        thinking_budgets: BTreeMap<String, u64>,
    ) -> anyhow::Result<Self> {
        let base = base_url.trim_end_matches('/');
        let endpoint = if base.ends_with("/v1/messages") {
            base.to_string()
        } else if base.ends_with("/v1") {
            format!("{base}/messages")
        } else {
            format!("{base}/v1/messages")
        };
        Ok(Self {
            name,
            endpoint,
            api_key,
            version,
            max_output_tokens: max_output_tokens.unwrap_or(DEFAULT_MAX_OUTPUT_TOKENS),
            thinking_budgets,
            client: reqwest::Client::builder()
                .connect_timeout(CONNECT_TIMEOUT)
                .build()?,
            read_idle_timeout: READ_IDLE_TIMEOUT,
        })
    }

    fn build_body(&self, req: &CompletionRequest<'_>) -> Result<Value, ProviderError> {
        let mut body = Map::new();
        body.insert("model".into(), json!(req.model));
        body.insert("messages".into(), Value::Array(wire_messages(req.messages)));
        body.insert("max_tokens".into(), json!(self.max_output_tokens));
        body.insert("stream".into(), json!(true));
        if !req.system.is_empty() {
            body.insert("system".into(), json!(req.system));
        }
        if !req.tools.is_empty() {
            body.insert(
                "tools".into(),
                Value::Array(
                    req.tools
                        .iter()
                        .map(|tool| {
                            json!({
                                "name": tool.name,
                                "description": tool.description,
                                "input_schema": tool.parameters,
                            })
                        })
                        .collect(),
                ),
            );
        }
        if let Some(level) = req.thinking {
            let budget = self.thinking_budgets.get(level).ok_or_else(|| {
                ProviderError::Call(format!(
                    "thinking level {level:?} has no budget configured for {}/{}",
                    self.name, req.model
                ))
            })?;
            if *budget >= self.max_output_tokens {
                return Err(ProviderError::Call(format!(
                    "thinking budget {budget} must be below maxOutputTokens {}",
                    self.max_output_tokens
                )));
            }
            body.insert(
                "thinking".into(),
                json!({"type": "enabled", "budget_tokens": budget}),
            );
        }
        Ok(Value::Object(body))
    }
}

impl Provider for AnthropicProvider {
    async fn complete(&self, req: &CompletionRequest<'_>) -> Result<Message, ProviderError> {
        let body = serde_json::to_vec(&self.build_body(req)?)
            .expect("Anthropic request body serialization cannot fail");
        let started = Instant::now();
        let response = self
            .client
            .post(&self.endpoint)
            .header("x-api-key", &self.api_key)
            .header("anthropic-version", &self.version)
            .header(reqwest::header::CONTENT_TYPE, "application/json")
            .body(body)
            .send()
            .await
            .map_err(classify_transport)?;
        if !response.status().is_success() {
            let status = response.status().as_u16();
            let body = response.text().await.unwrap_or_default();
            return Err(classify_http(status, &body));
        }
        let (aggregate, first_chunk_at) = read_stream(response, self.read_idle_timeout).await?;
        let ended = Instant::now();
        let stop_reason = match aggregate.stop_reason.as_deref() {
            Some("end_turn" | "stop_sequence" | "pause_turn") => StopReason::Stop,
            Some("tool_use") => StopReason::ToolUse,
            Some("max_tokens" | "model_context_window_exceeded") => StopReason::Length,
            Some(other) => {
                return Err(ProviderError::Call(format!(
                    "unexpected Anthropic stop_reason {other:?}"
                )))
            }
            None => {
                return Err(ProviderError::Transient(
                    "Anthropic stream ended without stop_reason".into(),
                ))
            }
        };
        let mut aggregate = aggregate;
        let (content, provider_data) = aggregate.take_content();
        let mut message = Message::bare(Role::Assistant, content);
        message.provider_data = provider_data;
        message.provider = Some(self.name.clone());
        message.model = Some(req.model.to_string());
        message.stop_reason = Some(stop_reason);
        message.usage = Some(Usage {
            input: aggregate
                .input_tokens
                .saturating_add(aggregate.cache_creation_tokens),
            output: aggregate.output_tokens,
            cache_read: aggregate.cache_read_tokens,
        });
        let first = first_chunk_at.unwrap_or(started);
        message.timing = Some(RequestTiming {
            ttfb_ms: millis(first.saturating_duration_since(started)),
            stream_ms: millis(ended.saturating_duration_since(first)),
            total_ms: millis(ended.saturating_duration_since(started)),
        });
        Ok(message)
    }
}

fn wire_messages(messages: &[Message]) -> Vec<Value> {
    let mut wire = Vec::new();
    let mut index = 0;
    while index < messages.len() {
        if messages[index].role == Role::ToolResult {
            let mut content = Vec::new();
            while index < messages.len() && messages[index].role == Role::ToolResult {
                content.push(tool_result_block(&messages[index]));
                index += 1;
            }
            wire.push(json!({"role": "user", "content": content}));
            continue;
        }
        wire.push(wire_message(&messages[index]));
        index += 1;
    }
    wire
}

fn tool_result_block(message: &Message) -> Value {
    json!({
        "type": "tool_result",
        "tool_use_id": message.tool_call_id.as_deref().unwrap_or(""),
        "content": joined_text(message),
        "is_error": message.is_error.unwrap_or(false),
    })
}

fn wire_message(message: &Message) -> Value {
    match message.role {
        Role::User => json!({"role": "user", "content": joined_text(message)}),
        Role::ToolResult => json!({"role": "user", "content": [tool_result_block(message)]}),
        Role::Assistant => {
            let mut content = Vec::new();
            if let Some(blocks) = message
                .provider_data
                .as_ref()
                .and_then(|data| data.get("anthropicThinkingBlocks"))
                .and_then(Value::as_array)
            {
                content.extend(blocks.iter().cloned());
            }
            let text = joined_text(message);
            if !text.is_empty() {
                content.push(json!({"type": "text", "text": text}));
            }
            for block in &message.content {
                if let ContentBlock::ToolCall {
                    id,
                    name,
                    arguments,
                } = block
                {
                    content.push(json!({
                        "type": "tool_use",
                        "id": id,
                        "name": name,
                        "input": arguments,
                    }));
                }
            }
            json!({"role": "assistant", "content": content})
        }
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

#[derive(Default)]
struct Aggregate {
    blocks: Vec<AnthropicBlock>,
    stop_reason: Option<String>,
    input_tokens: u64,
    output_tokens: u64,
    cache_read_tokens: u64,
    cache_creation_tokens: u64,
}

#[derive(Default)]
struct AnthropicBlock {
    kind: String,
    text: String,
    id: String,
    name: String,
    partial_json: String,
    signature: String,
    data: String,
}

impl Aggregate {
    fn apply(&mut self, event: &Value) -> Result<(), ProviderError> {
        match event.get("type").and_then(Value::as_str).unwrap_or("") {
            "message_start" => {
                if let Some(usage) = event.get("message").and_then(|v| v.get("usage")) {
                    self.apply_usage(usage);
                }
            }
            "content_block_start" => {
                let index = event_index(event)?;
                self.ensure(index)?;
                let block = event.get("content_block").unwrap_or(&Value::Null);
                let target = &mut self.blocks[index];
                target.kind = string_field(block, "type");
                // #637: every gateway-controlled string funnels through
                // push_bounded — content_block_start REPLACES the fields, but
                // a block re-started with a huge payload is the same flood.
                push_bounded(&mut target.text, &string_field(block, "text"))?;
                target.id = string_field(block, "id");
                target.name = string_field(block, "name");
                push_bounded(&mut target.signature, &string_field(block, "signature"))?;
                push_bounded(&mut target.data, &string_field(block, "data"))?;
                if let Some(input) = block.get("input").filter(|value| {
                    !value.is_null() && value.as_object().is_none_or(|object| !object.is_empty())
                }) {
                    push_bounded(&mut target.partial_json, &input.to_string())?;
                }
            }
            "content_block_delta" => {
                let index = event_index(event)?;
                self.ensure(index)?;
                let delta = event.get("delta").unwrap_or(&Value::Null);
                let target = &mut self.blocks[index];
                match delta.get("type").and_then(Value::as_str).unwrap_or("") {
                    "text_delta" => {
                        push_bounded(&mut target.text, &string_field(delta, "text"))?
                    }
                    "thinking_delta" => {
                        push_bounded(&mut target.text, &string_field(delta, "thinking"))?
                    }
                    "input_json_delta" => push_bounded(
                        &mut target.partial_json,
                        &string_field(delta, "partial_json"),
                    )?,
                    "signature_delta" => {
                        push_bounded(&mut target.signature, &string_field(delta, "signature"))?
                    }
                    other => {
                        return Err(ProviderError::Call(format!(
                            "unknown Anthropic content delta {other:?}"
                        )))
                    }
                }
            }
            "message_delta" => {
                if let Some(reason) = event
                    .get("delta")
                    .and_then(|v| v.get("stop_reason"))
                    .and_then(Value::as_str)
                {
                    self.stop_reason = Some(reason.to_string());
                }
                if let Some(usage) = event.get("usage") {
                    self.apply_usage(usage);
                }
            }
            "content_block_stop" | "message_stop" | "ping" => {}
            "error" => {
                let detail = event
                    .get("error")
                    .and_then(|v| v.get("message"))
                    .and_then(Value::as_str)
                    .unwrap_or("unknown stream error");
                return Err(ProviderError::Call(format!(
                    "Anthropic stream error: {detail}"
                )));
            }
            other => {
                return Err(ProviderError::Call(format!(
                    "unknown Anthropic stream event {other:?}"
                )))
            }
        }
        // Whole-aggregate check AFTER every event: the single growth
        // chokepoint every bounded append and block resize passes through,
        // so the aggregate cannot creep past the cap by spreading bytes
        // across 1025 blocks that are each individually under the per-field
        // limit.
        self.check_total()
    }

    fn apply_usage(&mut self, usage: &Value) {
        let get = |key| usage.get(key).and_then(Value::as_u64).unwrap_or(0);
        self.input_tokens = self.input_tokens.max(get("input_tokens"));
        self.output_tokens = self.output_tokens.max(get("output_tokens"));
        self.cache_read_tokens = self.cache_read_tokens.max(get("cache_read_input_tokens"));
        self.cache_creation_tokens = self
            .cache_creation_tokens
            .max(get("cache_creation_input_tokens"));
    }

    /// 整个聚合的大小：每个 content block 的每个聚合字段（#637 全局
    /// 上限据此判定）。
    fn total_chars(&self) -> usize {
        self.blocks
            .iter()
            .map(|block| {
                block.text.len()
                    + block.partial_json.len()
                    + block.signature.len()
                    + block.data.len()
            })
            .sum()
    }

    /// Reject the stream once the WHOLE aggregate (not one field — see
    /// [`MAX_AGGREGATE_CHARS`]) passes the #637 cap. Checked after every
    /// applied event, so no path grows the aggregate past the cap.
    fn check_total(&self) -> Result<(), ProviderError> {
        if self.total_chars() > MAX_AGGREGATE_CHARS {
            return Err(ProviderError::Transient(format!(
                "streamed response aggregate exceeds the {MAX_AGGREGATE_CHARS} char cap"
            )));
        }
        Ok(())
    }

    fn ensure(&mut self, index: usize) -> Result<(), ProviderError> {
        if index > 1024 {
            return Err(ProviderError::Transient(format!(
                "Anthropic content block index {index} exceeds limit"
            )));
        }
        self.blocks.resize_with(index + 1, AnthropicBlock::default);
        Ok(())
    }

    fn take_content(&mut self) -> (Vec<ContentBlock>, Option<Value>) {
        let blocks = std::mem::take(&mut self.blocks);
        let mut content = Vec::new();
        let mut thinking_blocks = Vec::new();
        for block in blocks {
            match block.kind.as_str() {
                "text" => content.push(ContentBlock::Text { text: block.text }),
                "thinking" => {
                    thinking_blocks.push(json!({
                        "type": "thinking",
                        "thinking": block.text,
                        "signature": block.signature,
                    }));
                    content.push(ContentBlock::Thinking {
                        thinking: block.text,
                    });
                }
                "redacted_thinking" => thinking_blocks.push(json!({
                    "type": "redacted_thinking",
                    "data": block.data,
                })),
                "tool_use" => content.push(ContentBlock::ToolCall {
                    id: block.id,
                    name: block.name,
                    arguments: serde_json::from_str(&block.partial_json)
                        .unwrap_or(Value::String(block.partial_json)),
                }),
                _ => {}
            }
        }
        if content.is_empty() {
            content.push(ContentBlock::Text {
                text: String::new(),
            });
        }
        let provider_data = (!thinking_blocks.is_empty()).then(|| {
            json!({
                "anthropicThinkingBlocks": thinking_blocks,
            })
        });
        (content, provider_data)
    }
}

async fn read_stream(
    response: reqwest::Response,
    idle_timeout: Duration,
) -> Result<(Aggregate, Option<Instant>), ProviderError> {
    let mut aggregate = Aggregate::default();
    let mut buffer = Vec::<u8>::new();
    let mut first = None;
    let mut stream = response.bytes_stream();
    loop {
        let next = tokio::time::timeout(idle_timeout, stream.next())
            .await
            .map_err(|_| ProviderError::Transient("Anthropic stream idle timeout".into()))?;
        let Some(chunk) = next else { break };
        let chunk =
            chunk.map_err(|err| ProviderError::Transient(format!("transport error: {err}")))?;
        for line in push_lines_bounded(&mut buffer, &chunk)? {
            if apply_line(&mut aggregate, &line)? {
                first.get_or_insert_with(Instant::now);
            }
        }
    }
    if !buffer.is_empty() && apply_line(&mut aggregate, &String::from_utf8_lossy(&buffer))? {
        first.get_or_insert_with(Instant::now);
    }
    Ok((aggregate, first))
}

/// #637: append one raw chunk to the SSE line buffer and return every line
/// completed by it, rejecting an unterminated line longer than
/// [`MAX_SSE_LINE_BYTES`] instead of growing it without bound — a junk flood
/// with no newline never reaches the aggregate caps, so only this check
/// bounds it. The buffer is cleared before returning the error (a caller
/// that ever catches it and reuses the buffer starts from empty), mirroring
/// `SseLineBuffer::push` in `openai_compat/aggregate.rs`.
fn push_lines_bounded(buffer: &mut Vec<u8>, chunk: &[u8]) -> Result<Vec<String>, ProviderError> {
    buffer.extend_from_slice(chunk);
    if buffer.len() > MAX_SSE_LINE_BYTES {
        buffer.clear();
        return Err(ProviderError::Transient(format!(
            "SSE line exceeds {} bytes (stream corruption)",
            MAX_SSE_LINE_BYTES
        )));
    }
    let mut lines = Vec::new();
    while let Some(pos) = buffer.iter().position(|byte| *byte == b'\n') {
        let line: Vec<u8> = buffer.drain(..=pos).collect();
        lines.push(String::from_utf8_lossy(&line[..line.len() - 1]).into_owned());
    }
    Ok(lines)
}

/// Append a streamed delta to an aggregated string, rejecting the stream
/// once the field passes the #637 defensive cap (see
/// [`MAX_STREAMED_FIELD_CHARS`]) — without it an anomalous/hostile gateway
/// flooding `data:` deltas (the `max_tokens` request header only binds a
/// well-behaved server) would grow the field without bound. Mirrors
/// `Aggregated::push_bounded` in `openai_compat/aggregate.rs`.
fn push_bounded(target: &mut String, delta: &str) -> Result<(), ProviderError> {
    if target.len() + delta.len() > MAX_STREAMED_FIELD_CHARS {
        return Err(ProviderError::Transient(format!(
            "streamed response text exceeds the {} char aggregate cap",
            MAX_STREAMED_FIELD_CHARS
        )));
    }
    target.push_str(delta);
    Ok(())
}

fn apply_line(aggregate: &mut Aggregate, line: &str) -> Result<bool, ProviderError> {
    let Some(payload) = line.trim_end_matches('\r').strip_prefix("data:") else {
        return Ok(false);
    };
    let payload = payload.trim();
    if payload.is_empty() {
        return Ok(false);
    }
    let event: Value = serde_json::from_str(payload)
        .map_err(|err| ProviderError::Transient(format!("malformed Anthropic SSE data: {err}")))?;
    aggregate.apply(&event)?;
    Ok(true)
}

fn event_index(event: &Value) -> Result<usize, ProviderError> {
    event
        .get("index")
        .and_then(Value::as_u64)
        .and_then(|value| usize::try_from(value).ok())
        .ok_or_else(|| ProviderError::Call("Anthropic content event has no valid index".into()))
}

fn string_field(value: &Value, key: &str) -> String {
    value
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string()
}

fn classify_transport(err: reqwest::Error) -> ProviderError {
    ProviderError::Transient(format!("transport error: {err}"))
}

fn classify_http(status: u16, body: &str) -> ProviderError {
    let detail: String = body.chars().take(500).collect();
    let message = format!("HTTP {status}: {detail}");
    if status == 429 || status >= 500 {
        ProviderError::Transient(message)
    } else {
        ProviderError::Call(message)
    }
}

fn millis(duration: Duration) -> u64 {
    u64::try_from(duration.as_millis()).unwrap_or(u64::MAX)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::provider::ToolSpec;

    #[test]
    fn builds_messages_api_body_with_model_tools_and_thinking_budget() {
        let provider = AnthropicProvider::new(
            "anthropic".into(),
            "https://api.anthropic.com".into(),
            "key".into(),
            "2023-06-01".into(),
            Some(8192),
            BTreeMap::from([("high".into(), 4096)]),
        )
        .unwrap();
        let messages = vec![Message::user("hello".into())];
        let tools = vec![ToolSpec {
            name: "read".into(),
            description: "Read a file".into(),
            parameters: json!({"type":"object"}),
        }];
        let body = provider
            .build_body(&CompletionRequest {
                model: "claude-sonnet",
                system: "system",
                messages: &messages,
                tools: &tools,
                thinking: Some("high"),
            })
            .unwrap();
        assert_eq!(body["model"], "claude-sonnet");
        assert_eq!(body["max_tokens"], 8192);
        assert_eq!(body["thinking"]["budget_tokens"], 4096);
        assert_eq!(body["tools"][0]["input_schema"]["type"], "object");
    }

    #[test]
    fn maps_tool_result_and_tool_use_messages() {
        let assistant = Message::bare(
            Role::Assistant,
            vec![ContentBlock::ToolCall {
                id: "tool-1".into(),
                name: "read".into(),
                arguments: json!({"path":"a.txt"}),
            }],
        );
        assert_eq!(wire_message(&assistant)["content"][0]["type"], "tool_use");
        let result = Message::tool_result(
            "tool-1".into(),
            "read".into(),
            vec![ContentBlock::Text { text: "ok".into() }],
            false,
        );
        assert_eq!(wire_message(&result)["content"][0]["type"], "tool_result");
    }

    #[test]
    fn coalesces_consecutive_tool_results_into_one_user_message() {
        let results = vec![
            Message::tool_result(
                "tool-1".into(),
                "read".into(),
                vec![ContentBlock::Text { text: "a".into() }],
                false,
            ),
            Message::tool_result(
                "tool-2".into(),
                "read".into(),
                vec![ContentBlock::Text { text: "b".into() }],
                true,
            ),
        ];
        let wire = wire_messages(&results);
        assert_eq!(wire.len(), 1);
        assert_eq!(wire[0]["role"], "user");
        assert_eq!(wire[0]["content"].as_array().unwrap().len(), 2);
        assert_eq!(wire[0]["content"][1]["is_error"], true);
    }

    #[test]
    fn folds_streamed_text_tool_arguments_and_usage() {
        let mut aggregate = Aggregate::default();
        for event in [
            json!({"type":"message_start","message":{"usage":{"input_tokens":10,"cache_read_input_tokens":4}}}),
            json!({"type":"content_block_start","index":0,"content_block":{"type":"text","text":"Hi"}}),
            json!({"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"x","name":"read","input":{}}}),
            json!({"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\"path\":\"a\"}"}}),
            json!({"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":7}}),
        ] {
            aggregate.apply(&event).unwrap();
        }
        assert_eq!(aggregate.stop_reason.as_deref(), Some("tool_use"));
        assert_eq!(aggregate.cache_read_tokens, 4);
        let (content, _) = aggregate.take_content();
        assert!(matches!(&content[0], ContentBlock::Text { text } if text == "Hi"));
        assert!(
            matches!(&content[1], ContentBlock::ToolCall { arguments, .. } if arguments == &json!({"path":"a"}))
        );
    }

    #[test]
    fn sse_line_buffer_rejects_overlong_line() {
        // #637: an unterminated line longer than any legitimate SSE payload
        // is corruption — the line buffer must reject it instead of growing
        // without bound (a junk flood with no newline never reaches the
        // aggregate caps). Mirrors the openai_compat SseLineBuffer test.
        let mut buffer = Vec::new();
        let junk = vec![b'x'; 2 * 1024 * 1024 + 1];
        let err = push_lines_bounded(&mut buffer, &junk)
            .expect_err("overlong line must be rejected");
        assert!(err.is_retryable(), "overlong line is transient: {err}");
        assert!(err.to_string().contains("SSE line exceeds"), "got: {err}");
        // The rejected buffer is cleared, not retained (defensive: a reused
        // buffer must not immediately re-trip on the stale junk).
        assert!(
            buffer.is_empty(),
            "buffer must be cleared on rejection, {} bytes retained",
            buffer.len()
        );

        // Newline-terminated lines drain on every push, so the same total
        // volume never trips the cap — only an UNTERMINATED line does.
        let mut buffer = Vec::new();
        for _ in 0..5 {
            let lines = push_lines_bounded(&mut buffer, b"data: x\n").unwrap();
            assert_eq!(lines, vec!["data: x".to_string()]);
        }
        assert!(buffer.is_empty());
    }

    #[test]
    fn aggregate_text_delta_past_the_cap_is_transient() {
        // #637: the per-field cap — a text_delta flood pushing one block's
        // text past MAX_STREAMED_FIELD_CHARS is rejected as transient, not
        // grown without bound. The max_tokens request header only binds a
        // well-behaved server; this is the client-side bound.
        let mut aggregate = Aggregate::default();
        aggregate
            .apply(&json!({
                "type":"content_block_start","index":0,
                "content_block":{"type":"text","text":""}
            }))
            .unwrap();
        let mut err = None;
        // 33 × 1 MiB deltas push the text field past the 32 MiB cap.
        for _ in 0..33 {
            let event = json!({
                "type":"content_block_delta","index":0,
                "delta":{"type":"text_delta","text":"a".repeat(1024 * 1024)}
            });
            if let Err(e) = aggregate.apply(&event) {
                err = Some(e);
                break;
            }
        }
        let err = err.expect("per-field cap must fire before 33 MiB accumulates");
        assert!(err.is_retryable(), "over-cap stream is transient: {err}");
        assert!(err.to_string().contains("aggregate cap"), "got: {err}");
        // The rejected delta was never appended — the field stays capped.
        assert!(aggregate.blocks[0].text.len() <= 32 * 1024 * 1024);
    }

    #[test]
    fn aggregate_many_small_blocks_hit_the_global_cap() {
        // #637 compound-storm regression: 33 blocks × 1 MiB text each —
        // every single field is UNDER MAX_STREAMED_FIELD_CHARS (so the
        // per-field cap alone passes), while the whole aggregate crosses
        // 32 MiB. Only the WHOLE-aggregate cap rejects this shape. Mirrors
        // the openai_compat many-small-tool-calls test.
        let mut aggregate = Aggregate::default();
        let mut err = None;
        for index in 0..40u64 {
            let event = json!({
                "type":"content_block_start","index":index,
                "content_block":{"type":"text","text":""}
            });
            if let Err(e) = aggregate.apply(&event) {
                err = Some(e);
                break;
            }
            let event = json!({
                "type":"content_block_delta","index":index,
                "delta":{"type":"text_delta","text":"c".repeat(1024 * 1024)}
            });
            if let Err(e) = aggregate.apply(&event) {
                err = Some(e);
                break;
            }
        }
        let err = err.expect("global cap must fire on many small blocks");
        assert!(err.is_retryable(), "over-cap aggregate is transient: {err}");
        assert!(err.to_string().contains("aggregate exceeds"), "got: {err}");
        // The aggregate stays at/below the global cap plus one delta head.
        assert!(
            aggregate.total_chars() <= 32 * 1024 * 1024 + 1024 * 1024,
            "aggregate must stay bounded: {}",
            aggregate.total_chars()
        );
    }
}
