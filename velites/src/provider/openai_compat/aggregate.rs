//! SSE aggregation and wire serialization for the OpenAI-compatible
//! provider: the chunk-stream accumulator (text/thinking/tool_calls/usage),
//! the SSE line buffer, non-streaming body parsing, usage extraction, and
//! message/tool wire shapes. Split from ``openai_compat.rs`` for the file
//! size budget (#202); the provider struct, request building, and HTTP
//! error classification stay there.

use std::time::{Duration, Instant};

use futures_util::StreamExt;
use serde_json::{json, Map, Value};

use super::super::{ProviderError, ToolSpec};
use super::classify_transport_error;
use crate::events::{ContentBlock, Message, Role, Usage};

/// Aggregated state of one completion, built up from SSE chunks (or filled
/// at once from a non-streaming response).
#[derive(Default)]
pub(super) struct Aggregated {
    pub(super) text: String,
    pub(super) thinking: String,
    pub(super) tool_calls: Vec<ToolCallAcc>,
    pub(super) usage: Usage,
    pub(super) finish_reason: Option<String>,
}

#[derive(Default)]
pub(super) struct ToolCallAcc {
    pub(super) id: String,
    pub(super) name: String,
    pub(super) arguments: String,
}

/// Upper bound on a streamed `tool_calls[].index`. Real models emit a
/// handful of sequentially numbered calls; anything beyond this is stream
/// corruption (a mangled proxy chunk), rejected before `resize_with` could
/// turn it into an overflow/OOM panic.
const MAX_TOOL_CALL_INDEX: u64 = 1024;

/// 单次补全聚合文本的防御性上限（#637）：text/thinking/单个 tool_call
/// arguments 各自不超过 32 MiB。正常路径下模型输出受请求侧 max_tokens /
/// 服务端默认值约束，远够不到这里；该上限只拦网关/代理侧的异常流（没有
/// max_tokens 协商的 OpenAI 兼容端点、损坏的 chunk 泛洪），避免一个流把
/// 进程内存吃穿。32 MiB ≈ 800 万 token 的输出，误伤正常补全的概率为零。
const MAX_STREAMED_TEXT_CHARS: usize = 32 * 1024 * 1024;

/// 单次补全聚合的全局上限（#637）：per-field 上限只约束单个 String——
/// 一个异常流可以合法地填满 1025 个 tool_call（MAX_TOOL_CALL_INDEX 允许
/// 0..=1024）× 各 32 MiB arguments ≈ 32 GiB，per-field 检查全部通过。
/// 这就是 #637 事故的复合风暴面：上限必须同时封住 per-field 和 whole-
/// aggregate 两个维度。此处再高也会拦住任何字段触顶后继续堆积的流。
const MAX_AGGREGATE_CHARS: usize = 32 * 1024 * 1024;

impl Aggregated {
    pub(super) fn into_content(self) -> Vec<ContentBlock> {
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
            Self::push_bounded(&mut self.text, text)?;
        }
        let reasoning = delta
            .get("reasoning_content")
            .or_else(|| delta.get("reasoning"))
            .and_then(Value::as_str);
        if let Some(thinking) = reasoning {
            Self::push_bounded(&mut self.thinking, thinking)?;
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
                    Self::push_bounded(&mut acc.id, id)?;
                }
                if let Some(function) = call.get("function") {
                    if let Some(name) = function.get("name").and_then(Value::as_str) {
                        Self::push_bounded(&mut acc.name, name)?;
                    }
                    if let Some(arguments) = function.get("arguments").and_then(Value::as_str) {
                        Self::push_bounded(&mut acc.arguments, arguments)?;
                    }
                }
            }
        }
        // Whole-completion check AFTER the delta: this is the only place
        // every growth path (text, thinking, tool_calls resize, per-call
        // fields) is guaranteed to pass through, so the aggregate cannot
        // creep past the cap by spreading bytes across fields that are each
        // individually under the per-field limit.
        self.check_total()
    }

    /// 全局（whole-completion）聚合大小：text + thinking + 每个 tool_call
    /// 的 id/name/arguments。单次补全的总输出上限据此判定（#637）。
    pub(super) fn total_chars(&self) -> usize {
        let calls: usize = self
            .tool_calls
            .iter()
            .map(|call| call.id.len() + call.name.len() + call.arguments.len())
            .sum();
        self.text.len() + self.thinking.len() + calls
    }

    /// Reject the stream once the WHOLE aggregate (not one field — see
    /// [`MAX_AGGREGATE_CHARS`]) passes the #637 cap. Checked after every
    /// bounded append and after every tool_calls resize, so no path grows
    /// the aggregate past the cap.
    fn check_total(&self) -> Result<(), ProviderError> {
        if self.total_chars() > MAX_AGGREGATE_CHARS {
            return Err(ProviderError::Transient(format!(
                "streamed response aggregate exceeds the {MAX_AGGREGATE_CHARS} char cap"
            )));
        }
        Ok(())
    }

    /// Append a streamed delta to an aggregated string, rejecting the stream
    /// once the buffer passes the #637 defensive cap (see
    /// [`MAX_STREAMED_TEXT_CHARS`]) — without it a proxy/gateway streaming
    /// junk forever (the OpenAI-compatible path sends no max_tokens, so
    /// nothing else bounds the stream) would grow the aggregate unbounded.
    fn push_bounded(target: &mut String, delta: &str) -> Result<(), ProviderError> {
        if target.len() + delta.len() > MAX_STREAMED_TEXT_CHARS {
            return Err(ProviderError::Transient(format!(
                "streamed response text exceeds the {} char aggregate cap",
                MAX_STREAMED_TEXT_CHARS
            )));
        }
        target.push_str(delta);
        Ok(())
    }

    pub(super) fn apply_chunk(&mut self, chunk: &Value) -> Result<(), ProviderError> {
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
pub(super) fn extract_error_detail(value: &Value) -> Option<String> {
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
pub(super) fn parse_usage(usage: &Value) -> Usage {
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
pub(super) fn parse_non_streaming(body: &str) -> Result<Aggregated, ProviderError> {
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

/// One SSE data line (one JSON `data:` payload) is a few KB at most. A
/// stream that sends a line longer than this is corruption, and — no matter
/// its shape — the buffer must never hold it: `SseLineBuffer` accumulates
/// unterminated bytes verbatim, and the aggregate caps only bound the
/// PARSED payload (a non-JSON line never reaches them — but a `data:`-less
/// junk flood also never gets parsed, and would previously grow this buffer
/// without bound, #637's exact shape).
const MAX_SSE_LINE_BYTES: usize = 2 * 1024 * 1024;

/// Assemble SSE lines from raw byte chunks. Chunk boundaries may split a
/// multi-byte UTF-8 sequence, so decoding happens only once a full line
/// (newline-terminated) has arrived — a valid UTF-8 line never contains a
/// partial character.
#[derive(Default)]
pub(super) struct SseLineBuffer {
    buffer: Vec<u8>,
}

impl SseLineBuffer {
    /// Append one TCP chunk; returns every line completed by it.
    pub(super) fn push(&mut self, chunk: &[u8]) -> Result<Vec<String>, ProviderError> {
        self.buffer.extend_from_slice(chunk);
        // #637: an unterminated line longer than any legitimate SSE data
        // payload is stream corruption — reject instead of growing the
        // buffer without bound (a junk flood with no newline never reaches
        // the aggregate, so the aggregate caps cannot bound it). The buffer
        // is cleared before returning, so a caller that ever catches the
        // error and reuses the buffer starts from empty instead of
        // immediately re-tripping on the retained junk.
        if self.buffer.len() > MAX_SSE_LINE_BYTES {
            self.buffer.clear();
            return Err(ProviderError::Transient(format!(
                "SSE line exceeds {} bytes (stream corruption)",
                MAX_SSE_LINE_BYTES
            )));
        }
        let mut lines = Vec::new();
        while let Some(pos) = self.buffer.iter().position(|b| *b == b'\n') {
            let line: Vec<u8> = self.buffer.drain(..=pos).collect();
            lines.push(String::from_utf8_lossy(&line[..line.len() - 1]).into_owned());
        }
        Ok(lines)
    }

    /// Flush a trailing line without a newline terminator, if any.
    pub(super) fn finish(&mut self) -> Option<String> {
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
pub(super) async fn read_sse_stream(
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
        for line in lines.push(&chunk)? {
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
pub(super) fn apply_sse_line(
    aggregated: &mut Aggregated,
    line: &str,
) -> Result<bool, ProviderError> {
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
pub(super) fn wire_message(message: &Message) -> Value {
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

pub(super) fn wire_tool(spec: &ToolSpec) -> Value {
    json!({
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    })
}

/// Char-boundary-safe truncation for error snippets.
fn truncate(text: &str, max: usize) -> &str {
    match text.char_indices().nth(max) {
        Some((index, _)) => &text[..index],
        None => text,
    }
}
