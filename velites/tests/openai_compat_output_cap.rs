//! Per-call output cap on the OpenAI-compatible wire (#952 / #1093): which
//! request field carries `--max-output-tokens`. Split out of
//! `openai_compat.rs` (past the 800-line test-file threshold) by topic; the
//! cases moved over unchanged. No real LLM calls.

#[allow(dead_code)]
mod common;

use common::{MockResponse, MockServer};
use serde_json::{json, Value};
use velites::events::Message;
use velites::models::OutputTokensParam;
use velites::provider::openai_compat::OpenAiCompatProvider;
use velites::provider::{CompletionRequest, Provider};

fn provider(server: &MockServer) -> OpenAiCompatProvider {
    OpenAiCompatProvider::new("gateway".into(), server.url.clone(), "sk-test".into()).unwrap()
}

fn request(messages: &[Message]) -> CompletionRequest<'_> {
    CompletionRequest {
        model: "kimi-k2.6",
        system: "You are a test agent.",
        messages,
        tools: &[],
        thinking: None,
    }
}

/// A one-chunk SSE body that ends the turn immediately.
fn stop_body() -> String {
    let chunk: Value = json!({"choices": [{"delta": {}, "finish_reason": "stop"}]});
    format!("data: {chunk}\n\ndata: [DONE]\n\n")
}

#[tokio::test]
async fn max_output_tokens_is_sent_as_max_tokens() {
    // #952: --max-output-tokens rides the wire as the per-call `max_tokens`.
    let server = MockServer::start(vec![MockResponse::sse(stop_body())]).await;

    let messages = vec![Message::user("hi".into())];
    provider(&server)
        .with_max_output_tokens(Some(32000))
        .complete(&request(&messages))
        .await
        .unwrap();

    let sent = server.recorded()[0].body_json();
    assert_eq!(sent["max_tokens"], 32000);
    assert!(sent.get("max_completion_tokens").is_none());
}

#[tokio::test]
async fn reasoning_model_sends_cap_as_max_completion_tokens() {
    // #1093: OpenAI reasoning models (o-series) reject `max_tokens`; a
    // registry declaring `outputTokensParam: max_completion_tokens` moves
    // the cap to that field and never sends both.
    let server = MockServer::start(vec![MockResponse::sse(stop_body())]).await;

    let messages = vec![Message::user("hi".into())];
    let mut req = request(&messages);
    req.model = "o3-mini";
    req.thinking = Some("high");
    provider(&server)
        .with_max_output_tokens(Some(32000))
        .with_output_tokens_param(OutputTokensParam::MaxCompletionTokens)
        .complete(&req)
        .await
        .unwrap();

    let sent = server.recorded()[0].body_json();
    assert_eq!(sent["max_completion_tokens"], 32000);
    assert!(sent.get("max_tokens").is_none());
    assert_eq!(sent["reasoning_effort"], "high");
}

#[tokio::test]
async fn output_tokens_param_without_cap_sends_neither_field() {
    // #1093: the declaration only picks the field; without a cap the wire
    // shape stays the historical one (no cap field at all).
    let server = MockServer::start(vec![MockResponse::sse(stop_body())]).await;

    let messages = vec![Message::user("hi".into())];
    provider(&server)
        .with_output_tokens_param(OutputTokensParam::MaxCompletionTokens)
        .complete(&request(&messages))
        .await
        .unwrap();

    let sent = server.recorded()[0].body_json();
    assert!(sent.get("max_tokens").is_none());
    assert!(sent.get("max_completion_tokens").is_none());
}
