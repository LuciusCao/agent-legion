//! OpenAI-compat retry observability: pi-compatible auto_retry_start events.
//!
//! Split from ``openai_compat.rs`` when the shared file passed its 800-line
//! split threshold (#450 follow-up, AGENTS.md §4 — same-dir sibling split,
//! cases moved verbatim).

#[allow(dead_code)]
mod common;

use std::time::Duration;

use common::{MockResponse, MockServer};
use serde_json::{json, Value};
use velites::events::{
    AutoRetryStartEvent, Event, EventSink, Message, SharedMemorySink, StopReason,
};
use velites::provider::openai_compat::OpenAiCompatProvider;
use velites::provider::retry::RetryProvider;
use velites::tools::ToolKind;

fn provider(server: &MockServer) -> OpenAiCompatProvider {
    OpenAiCompatProvider::new("gateway".into(), server.url.clone(), "sk-test".into()).unwrap()
}

fn sse_body(chunks: &[Value]) -> String {
    chunks
        .iter()
        .map(|chunk| format!("data: {chunk}\n\n"))
        .collect()
}

// --- Retry observability (pi-compatible auto_retry_start events) -----------

/// Retry provider whose failed transient attempts emit the pi-compatible
/// `message_end`(error) + `auto_retry_start` pair into the shared sink —
/// the same wiring `lib::run` uses with `StdoutJsonlSink`.
fn retrying_with_events(
    server: &MockServer,
    max_retries: u32,
    sink: SharedMemorySink,
) -> RetryProvider<OpenAiCompatProvider> {
    RetryProvider::new(provider(server), max_retries, Duration::from_millis(1))
        .with_on_attempt_failed(move |attempt, max_attempts, delay, err| {
            let events = velites::events::retry_attempt_events(
                "gateway",
                "kimi-k2.6",
                attempt,
                max_attempts,
                delay.as_millis() as u64,
                &err.to_string(),
            );
            let mut sink = sink.clone();
            for event in &events {
                sink.emit(event);
            }
        })
}

fn agent_config(dir: &tempfile::TempDir) -> velites::agent::AgentConfig {
    velites::agent::AgentConfig {
        name: Some("retry-obs".into()),
        provider_name: "gateway".into(),
        model: "kimi-k2.6".into(),
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
    }
}

fn event_types(events: &[Event]) -> Vec<&'static str> {
    events
        .iter()
        .map(|event| match event {
            Event::Session(_) => "session",
            Event::AgentStart(_) => "agent_start",
            Event::AgentEnd(_) => "agent_end",
            Event::TurnStart(_) => "turn_start",
            Event::TurnEnd(_) => "turn_end",
            Event::MessageStart(_) => "message_start",
            Event::MessageEnd(_) => "message_end",
            Event::AutoRetryStart(_) => "auto_retry_start",
            Event::ToolExecutionStart(_) => "tool_execution_start",
            Event::ToolExecutionEnd(_) => "tool_execution_end",
            Event::OutputsValidation(_) => "outputs_validation",
        })
        .collect()
}

#[tokio::test]
async fn retry_events_emitted_and_run_recovers() {
    // Two failed transient attempts (500, interrupted stream), third call
    // succeeds: two (error message_end + auto_retry_start) pairs, then the
    // normal completion — the Host clears the recorded error on the final
    // stop, exactly like the Node Pi retry pattern.
    let ok = sse_body(&[
        json!({"choices": [{"delta": {"content": "recovered"}, "finish_reason": "stop"}]}),
        json!({"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}),
    ]);
    let server = MockServer::start(vec![
        MockResponse::json(500, r#"{"error":{"message":"upstream boom"}}"#),
        MockResponse::truncated_sse(ok.clone(), 30),
        MockResponse::sse(ok),
    ])
    .await;

    let dir = tempfile::tempdir().unwrap();
    let mut sink = SharedMemorySink::default();
    let provider = retrying_with_events(&server, 3, sink.clone());
    let exit = velites::agent::run(agent_config(&dir), &provider, &mut sink)
        .await
        .unwrap();
    assert_eq!(exit, 0);

    let events = sink.events.lock().unwrap();
    assert_eq!(
        event_types(&events),
        vec![
            "session",
            "agent_start",
            "turn_start",
            "message_start",
            "message_end",      // attempt 1 failed (HTTP 500)
            "auto_retry_start", // attempt 1
            "message_end",      // attempt 2 failed (interrupted stream)
            "auto_retry_start", // attempt 2
            "message_end",      // attempt 3 recovered
            "turn_end",
            "agent_end",
        ]
    );

    let message_ends: Vec<&Message> = events
        .iter()
        .filter_map(|event| match event {
            Event::MessageEnd(payload) => Some(&payload.message),
            _ => None,
        })
        .collect();
    assert_eq!(message_ends.len(), 3);
    for failed in &message_ends[..2] {
        assert_eq!(failed.stop_reason, Some(StopReason::Error));
        assert!(failed.error_message.is_some(), "errorMessage required");
        assert_eq!(failed.provider.as_deref(), Some("gateway"));
        assert_eq!(failed.model.as_deref(), Some("kimi-k2.6"));
        // Failed attempts carry no request-level timing.
        assert!(failed.timing.is_none(), "error events omit timing");
    }
    assert!(message_ends[0]
        .error_message
        .as_deref()
        .unwrap_or_default()
        .contains("500"));
    assert_eq!(message_ends[2].stop_reason, Some(StopReason::Stop));
    // Only the successful attempt is timed.
    let timing = message_ends[2]
        .timing
        .expect("recovered attempt carries timing");
    assert!(timing.ttfb_ms <= timing.total_ms);
    assert!(timing.stream_ms <= timing.total_ms);

    let attempts: Vec<u32> = events
        .iter()
        .filter_map(|event| match event {
            Event::AutoRetryStart(AutoRetryStartEvent { attempt, .. }) => Some(*attempt),
            _ => None,
        })
        .collect();
    assert_eq!(attempts, vec![1, 2]);

    let agent_end = events
        .iter()
        .find_map(|event| match event {
            Event::AgentEnd(payload) => Some(payload),
            _ => None,
        })
        .unwrap();
    assert!(
        agent_end.error.is_none(),
        "recovered run has no agent_end error"
    );
    assert_eq!(server.recorded().len(), 3, "2 failures + 1 success");
}

#[tokio::test]
async fn retry_events_exhausted_ends_with_terminal_error_exit_0() {
    // Every attempt fails transiently: N retry pairs, then the agent loop's
    // terminal error message_end + agent_end.error, exit still 0.
    let server = MockServer::start(vec![
        MockResponse::json(500, r#"{"error":{"message":"upstream boom"}}"#),
        MockResponse::json(500, r#"{"error":{"message":"upstream boom"}}"#),
        MockResponse::json(500, r#"{"error":{"message":"upstream boom"}}"#),
    ])
    .await;

    let dir = tempfile::tempdir().unwrap();
    let mut sink = SharedMemorySink::default();
    let provider = retrying_with_events(&server, 2, sink.clone());
    let exit = velites::agent::run(agent_config(&dir), &provider, &mut sink)
        .await
        .unwrap();
    assert_eq!(exit, 0);

    let events = sink.events.lock().unwrap();
    assert_eq!(
        event_types(&events),
        vec![
            "session",
            "agent_start",
            "turn_start",
            "message_start",
            "message_end",      // attempt 1 failed
            "auto_retry_start", // attempt 1
            "message_end",      // attempt 2 failed
            "auto_retry_start", // attempt 2
            "message_end",      // attempt 3 failed: retries exhausted, terminal
            "turn_end",
            "agent_end",
        ]
    );

    let message_ends: Vec<&Message> = events
        .iter()
        .filter_map(|event| match event {
            Event::MessageEnd(payload) => Some(&payload.message),
            _ => None,
        })
        .collect();
    assert_eq!(message_ends.len(), 3);
    for failed in &message_ends {
        assert_eq!(failed.stop_reason, Some(StopReason::Error));
        assert!(failed.error_message.is_some());
    }

    let retries = events
        .iter()
        .filter(|event| matches!(event, Event::AutoRetryStart(_)))
        .count();
    assert_eq!(retries, 2, "one auto_retry_start per retried attempt");

    let agent_end = events
        .iter()
        .find_map(|event| match event {
            Event::AgentEnd(payload) => Some(payload),
            _ => None,
        })
        .unwrap();
    assert!(agent_end
        .error
        .as_deref()
        .unwrap_or_default()
        .contains("500"));
    assert_eq!(server.recorded().len(), 3, "initial attempt + 2 retries");
}
