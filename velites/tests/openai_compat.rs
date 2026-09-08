//! Integration tests for the OpenAI-compatible provider against a local
//! mock HTTP/SSE server (tests/common). No real LLM calls.

mod common;

use std::time::Duration;

use common::{MockResponse, MockServer};
use serde_json::{json, Value};
use velites::events::{ContentBlock, Event, MemorySink, Message, StopReason};
use velites::provider::openai_compat::OpenAiCompatProvider;
use velites::provider::retry::RetryProvider;
use velites::provider::{CompletionRequest, Provider, ToolSpec};
use velites::tools::ToolKind;

fn provider(server: &MockServer) -> OpenAiCompatProvider {
    OpenAiCompatProvider::new("gateway".into(), server.url.clone(), "sk-test".into()).unwrap()
}

fn retrying(server: &MockServer, max_retries: u32) -> RetryProvider<OpenAiCompatProvider> {
    RetryProvider::new(provider(server), max_retries, Duration::from_millis(1))
}

fn request<'a>(messages: &'a [Message], tools: &'a [ToolSpec]) -> CompletionRequest<'a> {
    CompletionRequest {
        model: "kimi-k2.6",
        system: "You are a test agent.",
        messages,
        tools,
        thinking: None,
    }
}

fn sse_body(chunks: &[Value]) -> String {
    let mut body = String::new();
    for chunk in chunks {
        body.push_str("data: ");
        body.push_str(&chunk.to_string());
        body.push_str("\n\n");
    }
    body.push_str("data: [DONE]\n\n");
    body
}

#[tokio::test]
async fn streaming_chunks_aggregate_into_one_message() {
    let body = sse_body(&[
        json!({"choices": [{"delta": {"role": "assistant"}, "finish_reason": null}]}),
        json!({"choices": [{"delta": {"reasoning_content": "let me "}, "finish_reason": null}]}),
        json!({"choices": [{"delta": {"reasoning_content": "think"}, "finish_reason": null}]}),
        json!({"choices": [{"delta": {"content": "Hel"}, "finish_reason": null}]}),
        json!({"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]}),
        json!({"choices": [], "usage": {
            "prompt_tokens": 100, "completion_tokens": 12, "prompt_cache_hit_tokens": 64
        }}),
    ]);
    let server = MockServer::start(vec![MockResponse::sse(body)]).await;

    let messages = vec![Message::user("hi".into())];
    let message = provider(&server)
        .complete(&request(&messages, &[]))
        .await
        .unwrap();

    assert_eq!(message.stop_reason, Some(StopReason::Stop));
    assert_eq!(message.provider.as_deref(), Some("gateway"));
    assert_eq!(message.model.as_deref(), Some("kimi-k2.6"));
    let usage = message.usage.unwrap();
    // pi 口径: input excludes cache-read tokens (100 − 64).
    assert_eq!((usage.input, usage.output, usage.cache_read), (36, 12, 64));
    assert_eq!(
        message.content,
        vec![
            ContentBlock::Thinking {
                thinking: "let me think".into()
            },
            ContentBlock::Text {
                text: "Hello".into()
            },
        ]
    );

    // Request shape: streaming + usage opt-in, system first, bearer auth.
    let recorded = server.recorded();
    assert_eq!(recorded.len(), 1);
    let sent = &recorded[0];
    assert_eq!(sent.method, "POST");
    assert_eq!(sent.path, "/chat/completions");
    assert_eq!(sent.header("authorization"), Some("Bearer sk-test"));
    let sent = sent.body_json();
    assert_eq!(sent["model"], "kimi-k2.6");
    assert_eq!(sent["stream"], true);
    assert_eq!(sent["stream_options"]["include_usage"], true);
    assert_eq!(
        sent["messages"][0],
        json!({"role": "system", "content": "You are a test agent."})
    );
    assert_eq!(
        sent["messages"][1],
        json!({"role": "user", "content": "hi"})
    );
    assert!(sent.get("tools").is_none());
    assert!(sent.get("reasoning_effort").is_none());
}

#[tokio::test]
async fn thinking_flag_maps_to_reasoning_effort() {
    let body = sse_body(&[json!({"choices": [{"delta": {}, "finish_reason": "stop"}]})]);
    let server = MockServer::start(vec![MockResponse::sse(body)]).await;

    let messages = vec![Message::user("hi".into())];
    let mut req = request(&messages, &[]);
    req.thinking = Some("low");
    provider(&server).complete(&req).await.unwrap();

    let sent = server.recorded()[0].body_json();
    assert_eq!(sent["reasoning_effort"], "low");
}

#[tokio::test]
async fn tool_calls_aggregate_across_chunks() {
    let body = sse_body(&[
        json!({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_abc", "function": {"name": "read", "arguments": "{\"pa"}}
        ]}, "finish_reason": null}]}),
        json!({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "th\": \"in.txt\"}"}}
        ]}, "finish_reason": null}]}),
        json!({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        json!({"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 3}}),
    ]);
    let server = MockServer::start(vec![MockResponse::sse(body)]).await;

    let tools = vec![ToolSpec {
        name: "read".into(),
        description: "read a file".into(),
        parameters: json!({"type": "object"}),
    }];
    let messages = vec![Message::user("read it".into())];
    let message = provider(&server)
        .complete(&request(&messages, &tools))
        .await
        .unwrap();

    assert_eq!(message.stop_reason, Some(StopReason::ToolUse));
    assert_eq!(
        message.content,
        vec![ContentBlock::ToolCall {
            id: "call_abc".into(),
            name: "read".into(),
            arguments: json!({"path": "in.txt"}),
        }]
    );

    // Tool specs go out in the OpenAI function shape.
    let sent = server.recorded()[0].body_json();
    assert_eq!(
        sent["tools"][0],
        json!({
            "type": "function",
            "function": {"name": "read", "description": "read a file",
                         "parameters": {"type": "object"}},
        })
    );
}

#[tokio::test]
async fn non_streaming_json_fallback() {
    // PoC P2 dialect: the model answers application/json despite stream:true.
    let body = json!({
        "choices": [{
            "message": {"role": "assistant", "content": "plain answer"},
            "finish_reason": "stop"
        }],
        "usage": {"prompt_tokens": 7, "completion_tokens": 2,
                  "prompt_tokens_details": {"cached_tokens": 4}}
    })
    .to_string();
    let server = MockServer::start(vec![MockResponse::json(200, body)]).await;

    let messages = vec![Message::user("hi".into())];
    let message = provider(&server)
        .complete(&request(&messages, &[]))
        .await
        .unwrap();

    assert_eq!(message.stop_reason, Some(StopReason::Stop));
    assert_eq!(
        message.content,
        vec![ContentBlock::Text {
            text: "plain answer".into()
        }]
    );
    let usage = message.usage.unwrap();
    // pi 口径: input excludes cache-read tokens (7 − 4).
    assert_eq!((usage.input, usage.output, usage.cache_read), (3, 2, 4));
}

#[tokio::test]
async fn interrupted_stream_is_retried_and_recovers() {
    let full = sse_body(&[
        json!({"choices": [{"delta": {"content": "recovered"}, "finish_reason": "stop"}]}),
        json!({"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}),
    ]);
    let server = MockServer::start(vec![
        MockResponse::truncated_sse(full.clone(), 30),
        MockResponse::sse(full),
    ])
    .await;

    let messages = vec![Message::user("hi".into())];
    let message = retrying(&server, 3)
        .complete(&request(&messages, &[]))
        .await
        .unwrap();

    assert_eq!(message.stop_reason, Some(StopReason::Stop));
    assert_eq!(server.recorded().len(), 2, "interrupted attempt + retry");
}

#[tokio::test]
async fn gateway_close_before_any_chunk_is_transient_with_root_cause() {
    // The observed production failure (2026-08-01 replay): gateway answers
    // 200, goes silent ~52s, then closes the connection without a single
    // body byte. reqwest flattens this to "error decoding response body";
    // the source chain must surface hyper's root cause, and the error must
    // stay retryable (Host treats mid-connection failure as transient).
    let body = sse_body(&[json!({"choices": [{"delta": {}, "finish_reason": "stop"}]})]);
    let server = MockServer::start(vec![MockResponse::truncated_sse(body, 0)]).await;

    let messages = vec![Message::user("hi".into())];
    let err = provider(&server)
        .complete(&request(&messages, &[]))
        .await
        .unwrap_err();

    assert!(err.is_retryable(), "mid-connection close must retry: {err}");
    // The mock advertises Content-Length, so hyper's root cause is the
    // length-framing variant; a chunked gateway stream says "connection
    // closed before message completed" instead. Either way the cause must
    // be visible below reqwest's flattened top-level message.
    assert!(
        err.to_string()
            .contains("end of file before message length reached"),
        "root cause must be exposed, got: {err}"
    );
}

#[tokio::test]
async fn silent_stream_hits_read_idle_timeout() {
    // Headers arrive, then total silence: the per-read idle timeout fires
    // (transient) instead of hanging until the run deadline.
    let body = sse_body(&[json!({"choices": [{"delta": {}, "finish_reason": "stop"}]})]);
    let server = MockServer::start(vec![MockResponse::stalled_sse(
        body,
        Duration::from_secs(30),
    )])
    .await;
    let provider = provider(&server).with_read_idle_timeout(Duration::from_millis(100));

    let messages = vec![Message::user("hi".into())];
    let err = provider
        .complete(&request(&messages, &[]))
        .await
        .unwrap_err();

    assert!(err.is_retryable(), "idle timeout must be transient: {err}");
    assert!(err.to_string().contains("stream idle"), "got: {err}");
}

#[tokio::test]
async fn malformed_sse_data_chunk_is_transient_not_fatal() {
    // Proxy/gateway corruption can mangle a data payload's JSON. That is
    // stream corruption (retryable), not a deterministic model failure.
    let server = MockServer::start(vec![
        MockResponse::sse("data: {\"choices\":[{\"delta\":{\"content\":\"par"),
        MockResponse::sse(sse_body(&[json!({
            "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]
        })])),
    ])
    .await;

    let messages = vec![Message::user("hi".into())];
    let message = retrying(&server, 3)
        .complete(&request(&messages, &[]))
        .await
        .unwrap();

    assert_eq!(message.stop_reason, Some(StopReason::Stop));
    assert_eq!(server.recorded().len(), 2, "corrupted attempt + retry");
}

#[tokio::test]
async fn http_429_is_retried() {
    let server = MockServer::start(vec![
        MockResponse::json(429, r#"{"error":{"message":"rate limited"}}"#),
        MockResponse::sse(sse_body(&[json!({
            "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]
        })])),
    ])
    .await;

    let messages = vec![Message::user("hi".into())];
    let message = retrying(&server, 3)
        .complete(&request(&messages, &[]))
        .await
        .unwrap();
    assert_eq!(message.stop_reason, Some(StopReason::Stop));
    assert_eq!(server.recorded().len(), 2);
}

#[tokio::test]
async fn http_404_is_not_retried() {
    let server = MockServer::start(vec![MockResponse::json(
        404,
        r#"{"error":{"message":"Model no-such-model not found"}}"#,
    )])
    .await;

    let messages = vec![Message::user("hi".into())];
    let err = retrying(&server, 3)
        .complete(&request(&messages, &[]))
        .await
        .unwrap_err();

    assert!(!err.is_retryable());
    assert!(err.to_string().contains("404"), "got: {err}");
    assert!(err.to_string().contains("no-such-model"), "got: {err}");
    assert_eq!(
        server.recorded().len(),
        1,
        "deterministic 4xx must not retry"
    );
}

#[tokio::test]
async fn unrecovered_error_ends_run_with_exit_0() {
    // Full agent loop against a permanently failing gateway: the final
    // assistant message carries stopReason=error + errorMessage and the run
    // still returns exit code 0 (Pi semantics, design §4).
    let server = MockServer::start(vec![MockResponse::json(
        404,
        r#"{"error":{"message":"Model no-such-model not found"}}"#,
    )])
    .await;
    let provider = retrying(&server, 3);

    let dir = tempfile::tempdir().unwrap();
    let config = velites::agent::AgentConfig {
        name: Some("err-test".into()),
        provider_name: "gateway".into(),
        model: "no-such-model".into(),
        thinking: None,
        system_prompt: "sys".into(),
        instruction: "do something".into(),
        tools: vec![ToolKind::Read],
        budget: velites::budget::Budget::new(None, None, std::time::Duration::from_secs(600)),
        require_output: Vec::new(),
        session: None,
        cwd: dir.path().to_path_buf(),
        read_roots: Vec::new(),
        skill_dirs: Vec::new(),
        sandbox: None,
        cancel: velites::cancel::CancelToken::default(),
    };
    let mut sink = MemorySink::default();
    let exit = velites::agent::run(config, &provider, &mut sink)
        .await
        .unwrap();
    assert_eq!(exit, 0);

    let message_end = sink
        .events
        .iter()
        .find_map(|event| match event {
            Event::MessageEnd(payload) => Some(&payload.message),
            _ => None,
        })
        .expect("a message_end event must be emitted");
    assert_eq!(message_end.stop_reason, Some(StopReason::Error));
    let error = message_end.error_message.as_deref().unwrap_or_default();
    assert!(error.contains("404"), "got: {error}");
    assert!(error.contains("no-such-model"), "got: {error}");

    let agent_end = sink
        .events
        .iter()
        .find_map(|event| match event {
            Event::AgentEnd(payload) => Some(payload),
            _ => None,
        })
        .expect("an agent_end event must be emitted");
    assert!(agent_end
        .error
        .as_deref()
        .unwrap_or_default()
        .contains("404"));
    assert_eq!(server.recorded().len(), 1, "404 must not be retried");
}

#[tokio::test]
async fn full_tool_round_over_gateway() {
    // Turn 1: model asks for the read tool; turn 2: final answer. Asserts
    // the second request carries the assistant tool_calls and the tool
    // result message in the OpenAI wire shape.
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("in.txt"), "file-contents").unwrap();
    // The read tool canonicalizes paths before the sandbox check; macOS
    // tempdirs live under a /var → /private/var symlink, so canonicalize
    // the cwd the same way lib::run does.
    let cwd = dir.path().canonicalize().unwrap();

    let first = sse_body(&[
        json!({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "function": {"name": "read", "arguments": "{\"path\": \"in.txt\"}"}}
        ]}, "finish_reason": null}]}),
        json!({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        json!({"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 2}}),
    ]);
    let second = sse_body(&[
        json!({"choices": [{"delta": {"content": "The file says file-contents."}, "finish_reason": "stop"}]}),
        json!({"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 5}}),
    ]);
    let server = MockServer::start(vec![MockResponse::sse(first), MockResponse::sse(second)]).await;
    let provider = retrying(&server, 0);

    let config = velites::agent::AgentConfig {
        name: Some("tool-round".into()),
        provider_name: "gateway".into(),
        model: "kimi-k2.6".into(),
        thinking: None,
        system_prompt: "sys".into(),
        instruction: "read in.txt".into(),
        tools: vec![ToolKind::Read],
        budget: velites::budget::Budget::new(Some(5), None, std::time::Duration::from_secs(600)),
        require_output: Vec::new(),
        session: None,
        cwd,
        read_roots: Vec::new(),
        skill_dirs: Vec::new(),
        sandbox: None,
        cancel: velites::cancel::CancelToken::default(),
    };
    let mut sink = MemorySink::default();
    let exit = velites::agent::run(config, &provider, &mut sink)
        .await
        .unwrap();
    assert_eq!(exit, 0);

    let recorded = server.recorded();
    assert_eq!(recorded.len(), 2);
    let second_request = recorded[1].body_json();
    let wire_messages = second_request["messages"].as_array().unwrap();
    // system, user, assistant(tool_calls), tool result.
    assert_eq!(wire_messages.len(), 4);
    assert_eq!(wire_messages[2]["role"], "assistant");
    assert_eq!(wire_messages[2]["tool_calls"][0]["id"], "call_1");
    assert_eq!(
        wire_messages[2]["tool_calls"][0]["function"]["arguments"],
        r#"{"path":"in.txt"}"#
    );
    assert_eq!(wire_messages[3]["role"], "tool");
    assert_eq!(wire_messages[3]["tool_call_id"], "call_1");
    assert!(
        wire_messages[3]["content"]
            .as_str()
            .unwrap_or_default()
            .contains("file-contents"),
        "tool result content: {}",
        wire_messages[3]["content"]
    );

    let final_message = sink
        .events
        .iter()
        .filter_map(|event| match event {
            Event::MessageEnd(payload) => Some(&payload.message),
            _ => None,
        })
        .next_back()
        .unwrap();
    assert_eq!(final_message.stop_reason, Some(StopReason::Stop));
    assert_eq!(
        final_message.content,
        vec![ContentBlock::Text {
            text: "The file says file-contents.".into()
        }]
    );
}

#[tokio::test]
async fn failed_tool_round_carries_error_prefix_to_next_request() {
    // #450: read 一个不存在的文件 → is_error=true 的 tool result。OpenAI
    // wire 的 tool 消息没有 is_error 字段，失败信号以 `[ERROR]` 文本前缀
    // 透传到下一个请求——不能静默丢失，否则模型把错误输出当成功结果继续
    // 推理。事件流仍原生携带 isError（golden events 不受影响）。
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path().canonicalize().unwrap();

    let first = sse_body(&[
        json!({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "function": {"name": "read", "arguments": "{\"path\": \"missing.txt\"}"}}
        ]}, "finish_reason": null}]}),
        json!({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        json!({"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 2}}),
    ]);
    let second = sse_body(&[
        json!({"choices": [{"delta": {"content": "The read failed."}, "finish_reason": "stop"}]}),
        json!({"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 5}}),
    ]);
    let server = MockServer::start(vec![MockResponse::sse(first), MockResponse::sse(second)]).await;
    let provider = retrying(&server, 0);

    let config = velites::agent::AgentConfig {
        name: Some("tool-round-error".into()),
        provider_name: "gateway".into(),
        model: "kimi-k2.6".into(),
        thinking: None,
        system_prompt: "sys".into(),
        instruction: "read missing.txt".into(),
        tools: vec![ToolKind::Read],
        budget: velites::budget::Budget::new(Some(5), None, std::time::Duration::from_secs(600)),
        require_output: Vec::new(),
        session: None,
        cwd,
        read_roots: Vec::new(),
        skill_dirs: Vec::new(),
        sandbox: None,
        cancel: velites::cancel::CancelToken::default(),
    };
    let mut sink = MemorySink::default();
    let exit = velites::agent::run(config, &provider, &mut sink)
        .await
        .unwrap();
    assert_eq!(exit, 0);

    // 事件流照常携带 isError（回传方向的修复不影响事件契约）。
    let tool_end = sink
        .events
        .iter()
        .find_map(|event| match event {
            Event::ToolExecutionEnd(payload) => Some(payload),
            _ => None,
        })
        .expect("a tool_execution_end event must be emitted");
    assert!(tool_end.is_error);

    let recorded = server.recorded();
    assert_eq!(recorded.len(), 2);
    let second_request = recorded[1].body_json();
    let wire_messages = second_request["messages"].as_array().unwrap();
    // system, user, assistant(tool_calls), tool result.
    assert_eq!(wire_messages.len(), 4);
    assert_eq!(wire_messages[3]["role"], "tool");
    assert_eq!(wire_messages[3]["tool_call_id"], "call_1");
    let content = wire_messages[3]["content"].as_str().unwrap_or_default();
    assert!(
        content.starts_with("[ERROR] "),
        "failed tool result must carry the [ERROR] prefix, got: {content}"
    );
    assert!(
        content.contains("No such file"),
        "underlying error text must stay readable, got: {content}"
    );

    // 成功路径的对照：上一个用例（full_tool_round_over_gateway）已断言
    // 成功 tool result 无前缀；此处只需确认错误路径改写 content 本身，
    // tool_call_id 与 role 不受影响。
}

#[tokio::test]
async fn sse_tolerates_dialect_noise_lines() {
    // Comments, event: lines, crlf endings, and data: without a space.
    let body = ": keep-alive\n\
                event: message\n\
                data:{\"choices\":[{\"delta\":{\"content\":\"ok\"},\"finish_reason\":\"stop\"}]}\r\n\r\n\
                data: [DONE]\r\n\r\n";
    let server = MockServer::start(vec![MockResponse::sse(body)]).await;

    let messages = vec![Message::user("hi".into())];
    let message = provider(&server)
        .complete(&request(&messages, &[]))
        .await
        .unwrap();
    assert_eq!(message.stop_reason, Some(StopReason::Stop));
    assert_eq!(
        message.content,
        vec![ContentBlock::Text { text: "ok".into() }]
    );
}

#[tokio::test]
async fn stream_ending_without_finish_reason_is_transient() {
    // The connection closes cleanly but no finish_reason / [DONE] ever
    // arrives — Pi's "Stream ended without finish_reason" case: retryable.
    let no_finish =
        MockResponse::sse("data: {\"choices\":[{\"delta\":{\"content\":\"partial\"}}]}\n\n");
    let server = MockServer::start(vec![no_finish.clone(), no_finish]).await;

    let messages = vec![Message::user("hi".into())];
    let err = retrying(&server, 1)
        .complete(&request(&messages, &[]))
        .await
        .unwrap_err();
    assert!(err.is_retryable());
    assert!(err.to_string().contains("finish_reason"), "got: {err}");
    assert_eq!(server.recorded().len(), 2, "initial attempt + one retry");
}

#[tokio::test]
async fn timing_present_and_monotonic_on_paced_stream() {
    // A paced fixture stream gives the timing fields a stable lower bound:
    // several 20ms pauses between body pieces, so the first→last chunk span
    // cannot collapse to zero even on a fast loopback.
    let body = sse_body(&[
        json!({"choices": [{"delta": {"content": "Hel"}, "finish_reason": null}]}),
        json!({"choices": [{"delta": {"content": "lo"}, "finish_reason": null}]}),
        json!({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        json!({"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}}),
    ]);
    let server = MockServer::start(vec![MockResponse::paced_sse(
        body,
        48,
        Duration::from_millis(20),
    )])
    .await;

    let messages = vec![Message::user("hi".into())];
    let message = provider(&server)
        .complete(&request(&messages, &[]))
        .await
        .unwrap();

    let timing = message.timing.expect("successful stream must carry timing");
    assert!(
        timing.ttfb_ms <= timing.total_ms,
        "ttfb {} <= total {}",
        timing.ttfb_ms,
        timing.total_ms
    );
    assert!(
        timing.stream_ms <= timing.total_ms,
        "stream {} <= total {}",
        timing.stream_ms,
        timing.total_ms
    );
    assert!(
        timing.ttfb_ms + timing.stream_ms <= timing.total_ms,
        "ttfb {} + stream {} <= total {}",
        timing.ttfb_ms,
        timing.stream_ms,
        timing.total_ms
    );
    // Several 20ms pauses fall strictly between the first and last chunk.
    assert!(
        timing.stream_ms >= 30,
        "paced stream must show a measurable span, got {}ms",
        timing.stream_ms
    );
}

#[tokio::test]
async fn timing_present_on_non_streaming_json_fallback() {
    // The application/json dialect (PoC P2) measures headers as the
    // first-byte mark; all three fields are still populated.
    let body = json!({
        "choices": [{
            "message": {"role": "assistant", "content": "plain answer"},
            "finish_reason": "stop"
        }],
        "usage": {"prompt_tokens": 7, "completion_tokens": 2}
    })
    .to_string();
    let server = MockServer::start(vec![MockResponse::json(200, body)]).await;

    let messages = vec![Message::user("hi".into())];
    let message = provider(&server)
        .complete(&request(&messages, &[]))
        .await
        .unwrap();

    let timing = message.timing.expect("json fallback must carry timing");
    assert!(timing.ttfb_ms <= timing.total_ms);
    assert!(timing.stream_ms <= timing.total_ms);
}
