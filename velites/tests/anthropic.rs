//! Integration tests for the Anthropic Messages provider against a local
//! mock HTTP/SSE server. No real LLM calls.

#[allow(dead_code)]
mod common;

use std::collections::BTreeMap;

use common::{MockResponse, MockServer};
use serde_json::{json, Value};
use velites::events::{ContentBlock, Message, StopReason};
use velites::provider::anthropic::AnthropicProvider;
use velites::provider::{CompletionRequest, Provider, ToolSpec};

fn sse_body(events: &[Value]) -> String {
    events
        .iter()
        .map(|event| format!("data: {event}\n\n"))
        .collect()
}

#[tokio::test]
async fn sends_messages_headers_tools_and_folds_stream() {
    let body = sse_body(&[
        json!({"type":"message_start","message":{"usage":{
            "input_tokens":10,"cache_creation_input_tokens":2,
            "cache_read_input_tokens":4
        }}}),
        json!({"type":"content_block_start","index":0,
            "content_block":{"type":"text","text":"Hel"}}),
        json!({"type":"content_block_delta","index":0,
            "delta":{"type":"text_delta","text":"lo"}}),
        json!({"type":"content_block_start","index":1,
            "content_block":{"type":"tool_use","id":"call-1","name":"read","input":{}}}),
        json!({"type":"content_block_delta","index":1,
            "delta":{"type":"input_json_delta","partial_json":"{\"path\":\"a.txt\"}"}}),
        json!({"type":"content_block_stop","index":1}),
        json!({"type":"message_delta","delta":{"stop_reason":"tool_use"},
            "usage":{"output_tokens":7}}),
        json!({"type":"message_stop"}),
    ]);
    let server = MockServer::start(vec![MockResponse::sse(body)]).await;
    let provider = AnthropicProvider::new(
        "anthropic".into(),
        server.url.clone(),
        "sk-ant-test".into(),
        "2023-06-01".into(),
        Some(8192),
        BTreeMap::from([("high".into(), 4096)]),
    )
    .unwrap();
    let messages = vec![Message::user("Use the tool".into())];
    let tools = vec![ToolSpec {
        name: "read".into(),
        description: "Read a file".into(),
        parameters: json!({"type":"object","properties":{"path":{"type":"string"}}}),
    }];

    let message = provider
        .complete(&CompletionRequest {
            model: "claude-sonnet",
            system: "Be precise.",
            messages: &messages,
            tools: &tools,
            thinking: Some("high"),
        })
        .await
        .unwrap();

    assert_eq!(message.provider.as_deref(), Some("anthropic"));
    assert_eq!(message.model.as_deref(), Some("claude-sonnet"));
    assert_eq!(message.stop_reason, Some(StopReason::ToolUse));
    assert_eq!(
        message.content,
        vec![
            ContentBlock::Text {
                text: "Hello".into()
            },
            ContentBlock::ToolCall {
                id: "call-1".into(),
                name: "read".into(),
                arguments: json!({"path":"a.txt"}),
            },
        ]
    );
    let usage = message.usage.unwrap();
    assert_eq!((usage.input, usage.output, usage.cache_read), (12, 7, 4));

    let recorded = server.recorded();
    assert_eq!(recorded.len(), 1);
    let sent = &recorded[0];
    assert_eq!(sent.method, "POST");
    assert_eq!(sent.path, "/v1/messages");
    assert_eq!(sent.header("x-api-key"), Some("sk-ant-test"));
    assert_eq!(sent.header("anthropic-version"), Some("2023-06-01"));
    let body = sent.body_json();
    assert_eq!(body["model"], "claude-sonnet");
    assert_eq!(body["system"], "Be precise.");
    assert_eq!(body["max_tokens"], 8192);
    assert_eq!(body["thinking"]["budget_tokens"], 4096);
    assert_eq!(body["tools"][0]["input_schema"]["type"], "object");
}
