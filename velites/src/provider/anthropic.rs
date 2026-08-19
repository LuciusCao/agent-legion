//! Anthropic Messages API provider with streaming tool-use support.

use std::collections::BTreeMap;
use std::time::{Duration, Instant};

use futures_util::StreamExt;
use serde_json::{json, Map, Value};

use super::{CompletionRequest, Provider, ProviderError};
use crate::events::{ContentBlock, Message, RequestTiming, Role, StopReason, Usage};

const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const READ_IDLE_TIMEOUT: Duration = Duration::from_secs(180);
const DEFAULT_MAX_OUTPUT_TOKENS: u64 = 8192;

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
        let mut message = Message::bare(Role::Assistant, aggregate.take_content());
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
                target.text.push_str(&string_field(block, "text"));
                target.id = string_field(block, "id");
                target.name = string_field(block, "name");
                if let Some(input) = block.get("input").filter(|value| {
                    !value.is_null() && value.as_object().is_none_or(|object| !object.is_empty())
                }) {
                    target.partial_json = input.to_string();
                }
            }
            "content_block_delta" => {
                let index = event_index(event)?;
                self.ensure(index)?;
                let delta = event.get("delta").unwrap_or(&Value::Null);
                let target = &mut self.blocks[index];
                match delta.get("type").and_then(Value::as_str).unwrap_or("") {
                    "text_delta" => target.text.push_str(&string_field(delta, "text")),
                    "thinking_delta" => target.text.push_str(&string_field(delta, "thinking")),
                    "input_json_delta" => target
                        .partial_json
                        .push_str(&string_field(delta, "partial_json")),
                    "signature_delta" => {}
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
        Ok(())
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

    fn ensure(&mut self, index: usize) -> Result<(), ProviderError> {
        if index > 1024 {
            return Err(ProviderError::Transient(format!(
                "Anthropic content block index {index} exceeds limit"
            )));
        }
        self.blocks.resize_with(index + 1, AnthropicBlock::default);
        Ok(())
    }

    fn take_content(&mut self) -> Vec<ContentBlock> {
        let blocks = std::mem::take(&mut self.blocks);
        let mut content = Vec::new();
        for block in blocks {
            match block.kind.as_str() {
                "text" => content.push(ContentBlock::Text { text: block.text }),
                "thinking" => content.push(ContentBlock::Thinking {
                    thinking: block.text,
                }),
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
        content
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
        buffer.extend_from_slice(&chunk);
        while let Some(pos) = buffer.iter().position(|byte| *byte == b'\n') {
            let line: Vec<u8> = buffer.drain(..=pos).collect();
            if apply_line(
                &mut aggregate,
                &String::from_utf8_lossy(&line[..line.len() - 1]),
            )? {
                first.get_or_insert_with(Instant::now);
            }
        }
    }
    if !buffer.is_empty() && apply_line(&mut aggregate, &String::from_utf8_lossy(&buffer))? {
        first.get_or_insert_with(Instant::now);
    }
    Ok((aggregate, first))
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
        let content = aggregate.take_content();
        assert!(matches!(&content[0], ContentBlock::Text { text } if text == "Hi"));
        assert!(
            matches!(&content[1], ContentBlock::ToolCall { arguments, .. } if arguments == &json!({"path":"a"}))
        );
    }
}
