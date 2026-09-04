//! Agent-loop library integration tests (design §4/§5): the `run()` loop
//! driven offline by a scripted in-process provider and an in-memory sink.
//!
//! These exercise the library surface (`velites::agent::run`) the same way
//! future embedders would; the pure private helpers (`enabled_tool_names`,
//! `outputs_remediation_message`, `resolve_required_outputs`) stay covered by
//! inline `#[cfg(test)]` unit tests in src/agent.rs.

use std::collections::VecDeque;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Duration;

use velites::agent::{run, AgentConfig};
use velites::budget::Budget;
use velites::cancel::CancelToken;
use velites::events::{
    ContentBlock, EndReason, Event, MemorySink, Message, MessageEndEvent, OutputsValidationEvent,
    Role, StopReason, ToolExecutionEndEvent, Usage,
};
use velites::provider::{CompletionRequest, Provider, ProviderError};
use velites::session::SessionLog;
use velites::tools::ToolKind;

use velites::agent::EXIT_MISSING_OUTPUTS;

/// Minimal offline agent-loop config: canonical cwd (the sandbox root),
/// no OS sandbox, no session mirror, generous budgets, a never-cancelled
/// token. Individual tests override the fields they exercise.
fn base_config(cwd: PathBuf) -> AgentConfig {
    AgentConfig {
        name: Some("inline-test".into()),
        provider_name: "stub".into(),
        model: "stub".into(),
        thinking: None,
        system_prompt: "test system prompt".into(),
        instruction: "test instruction".into(),
        tools: Vec::new(),
        budget: Budget::new(None, None, Duration::from_secs(3600)),
        require_output: Vec::new(),
        session: None,
        cwd,
        read_roots: Vec::new(),
        skill_dirs: Vec::new(),
        sandbox: None,
        cancel: CancelToken::new(),
    }
}

fn assistant_text(text: &str, stop_reason: StopReason) -> Message {
    let mut message = Message::bare(
        Role::Assistant,
        vec![ContentBlock::Text { text: text.into() }],
    );
    message.stop_reason = Some(stop_reason);
    message
}

fn assistant_tool_call(id: &str, name: &str, arguments: serde_json::Value) -> Message {
    let mut message = Message::bare(
        Role::Assistant,
        vec![ContentBlock::ToolCall {
            id: id.into(),
            name: name.into(),
            arguments,
        }],
    );
    message.stop_reason = Some(StopReason::ToolUse);
    message
}

/// Deterministic provider: replays scripted responses and records the
/// message history of every request it saw.
struct ScriptedProvider {
    responses: Mutex<VecDeque<Message>>,
    requests: Mutex<Vec<Vec<Message>>>,
}

impl ScriptedProvider {
    fn new(responses: Vec<Message>) -> Self {
        Self {
            responses: Mutex::new(responses.into()),
            requests: Mutex::new(Vec::new()),
        }
    }

    fn requests(&self) -> Vec<Vec<Message>> {
        self.requests.lock().expect("requests poisoned").clone()
    }
}

impl Provider for ScriptedProvider {
    async fn complete(&self, req: &CompletionRequest<'_>) -> Result<Message, ProviderError> {
        self.requests
            .lock()
            .expect("requests poisoned")
            .push(req.messages.to_vec());
        self.responses
            .lock()
            .expect("responses poisoned")
            .pop_front()
            .ok_or_else(|| ProviderError::Fixture("script exhausted".into()))
    }
}

struct FailingProvider;

impl Provider for FailingProvider {
    async fn complete(&self, _req: &CompletionRequest<'_>) -> Result<Message, ProviderError> {
        Err(ProviderError::Call("gateway exploded".into()))
    }
}

fn event_type(event: &Event) -> &'static str {
    match event {
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
    }
}

fn event_types(sink: &MemorySink) -> Vec<&'static str> {
    sink.events.iter().map(event_type).collect()
}

fn turn_starts(sink: &MemorySink) -> usize {
    event_types(sink)
        .into_iter()
        .filter(|kind| *kind == "turn_start")
        .count()
}

fn tool_execution_ends(sink: &MemorySink) -> Vec<&ToolExecutionEndEvent> {
    sink.events
        .iter()
        .filter_map(|event| match event {
            Event::ToolExecutionEnd(event) => Some(event),
            _ => None,
        })
        .collect()
}

/// Session mirror messages (`agent_end` carries no history since schema
/// v2, so injected user notices are verified via the session log).
fn session_messages(dir: &std::path::Path) -> Vec<Message> {
    std::fs::read_to_string(dir.join("session/session.jsonl"))
        .expect("session log missing")
        .lines()
        .map(|line| serde_json::from_str(line).expect("session lines are NDJSON Messages"))
        .collect()
}

#[tokio::test]
async fn single_turn_run_emits_the_canonical_event_sequence() {
    let dir = tempfile::tempdir().unwrap();
    let config = base_config(dir.path().canonicalize().unwrap());
    let provider = ScriptedProvider::new(vec![assistant_text("done", StopReason::Stop)]);
    let mut sink = MemorySink::default();

    let code = run(config, &provider, &mut sink).await.unwrap();
    assert_eq!(code, 0);
    assert_eq!(
        event_types(&sink),
        vec![
            "session",
            "agent_start",
            "turn_start",
            "message_start",
            "message_end",
            "turn_end",
            "agent_end",
        ]
    );

    // The session event carries --name; the message_start skeleton
    // announces provider/model before any content exists.
    match &sink.events[0] {
        Event::Session(event) => assert_eq!(event.session_id, "inline-test"),
        other => panic!("expected session event, got {other:?}"),
    }
    match &sink.events[3] {
        Event::MessageStart(event) => {
            assert_eq!(event.message.role, Role::Assistant);
            assert!(event.message.content.is_empty());
            assert_eq!(event.message.provider.as_deref(), Some("stub"));
            assert_eq!(event.message.model.as_deref(), Some("stub"));
        }
        other => panic!("expected message_start event, got {other:?}"),
    }
    match sink.events.last().unwrap() {
        Event::AgentEnd(event) => {
            assert_eq!(event.error, None);
            assert_eq!(event.reason, None);
        }
        other => panic!("expected agent_end event, got {other:?}"),
    }
}

#[tokio::test]
async fn tool_round_executes_enabled_tool_and_feeds_result_back() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path().canonicalize().unwrap();
    let mut config = base_config(cwd);
    config.tools = vec![ToolKind::Write];

    let provider = ScriptedProvider::new(vec![
        assistant_tool_call(
            "call-0-0",
            "write",
            serde_json::json!({"path": "out.txt", "content": "payload"}),
        ),
        assistant_text("written", StopReason::Stop),
    ]);
    let mut sink = MemorySink::default();

    let code = run(config, &provider, &mut sink).await.unwrap();
    assert_eq!(code, 0);

    // The write really landed inside the sandbox root.
    assert_eq!(
        std::fs::read_to_string(dir.path().join("out.txt")).unwrap(),
        "payload"
    );

    assert_eq!(
        event_types(&sink),
        vec![
            "session",
            "agent_start",
            "turn_start",
            "message_start",
            "message_end",
            "tool_execution_start",
            "tool_execution_end",
            "turn_end",
            "turn_start",
            "message_start",
            "message_end",
            "turn_end",
            "agent_end",
        ]
    );

    let tool_ends = tool_execution_ends(&sink);
    assert_eq!(tool_ends.len(), 1);
    assert_eq!(tool_ends[0].tool_call_id, "call-0-0");
    assert_eq!(tool_ends[0].tool_name, "write");
    assert!(!tool_ends[0].is_error);
    // For write the meaningful volume is the content written.
    assert_eq!(tool_ends[0].output_bytes, "payload".len() as u64);

    // The second model call saw the tool result appended to the history.
    let requests = provider.requests();
    assert_eq!(requests.len(), 2);
    assert_eq!(requests[1].len(), 3);
    assert_eq!(requests[1][0].role, Role::User);
    assert_eq!(requests[1][1].role, Role::Assistant);
    assert_eq!(requests[1][2].role, Role::ToolResult);
    assert_eq!(requests[1][2].tool_call_id.as_deref(), Some("call-0-0"));
    assert_eq!(requests[1][2].tool_name.as_deref(), Some("write"));
    assert_eq!(requests[1][2].is_error, Some(false));
}

#[tokio::test]
async fn disabled_tool_reports_error_result_without_failing_the_run() {
    let dir = tempfile::tempdir().unwrap();
    let mut config = base_config(dir.path().canonicalize().unwrap());
    config.tools = vec![ToolKind::Read];

    let provider = ScriptedProvider::new(vec![
        assistant_tool_call(
            "call-0-0",
            "write",
            serde_json::json!({"path": "out.txt", "content": "payload"}),
        ),
        assistant_text("noted", StopReason::Stop),
    ]);
    let mut sink = MemorySink::default();

    let code = run(config, &provider, &mut sink).await.unwrap();
    // A tool error is model feedback, not a harness fault: exit stays 0.
    assert_eq!(code, 0);
    assert!(!dir.path().join("out.txt").exists());

    let tool_ends = tool_execution_ends(&sink);
    assert_eq!(tool_ends.len(), 1);
    assert!(tool_ends[0].is_error);
    match &tool_ends[0].result.content[0] {
        ContentBlock::Text { text } => {
            assert!(text.contains("`write` is not enabled"));
            assert!(text.contains("enabled: read"));
        }
        other => panic!("expected text content, got {other:?}"),
    }
}

#[tokio::test]
async fn provider_failure_becomes_stop_reason_error_with_exit_zero() {
    let dir = tempfile::tempdir().unwrap();
    let config = base_config(dir.path().canonicalize().unwrap());
    let mut sink = MemorySink::default();

    // Pi semantics: an unrecovered model failure is data, not a harness
    // fault — the run ends exit 0 and the Host judges from the stream.
    let code = run(config, &FailingProvider, &mut sink).await.unwrap();
    assert_eq!(code, 0);

    let message_ends: Vec<&MessageEndEvent> = sink
        .events
        .iter()
        .filter_map(|event| match event {
            Event::MessageEnd(event) => Some(event),
            _ => None,
        })
        .collect();
    assert_eq!(message_ends.len(), 1);
    assert_eq!(message_ends[0].message.stop_reason, Some(StopReason::Error));
    assert_eq!(
        message_ends[0].message.error_message.as_deref(),
        Some("provider call failed: gateway exploded")
    );
    assert_eq!(message_ends[0].message.usage, Some(Usage::default()));

    match sink.events.last().unwrap() {
        Event::AgentEnd(event) => {
            assert_eq!(
                event.error.as_deref(),
                Some("provider call failed: gateway exploded")
            );
            assert_eq!(event.reason, None);
        }
        other => panic!("expected agent_end event, got {other:?}"),
    }
}

#[tokio::test]
async fn cancelled_run_ends_before_the_first_turn_and_skips_output_contract() {
    let dir = tempfile::tempdir().unwrap();
    let mut config = base_config(dir.path().canonicalize().unwrap());
    // Cancellation is exempt from the output contract: a deliberate Host
    // action, not a failed run — even with declared artifacts missing.
    config.require_output = vec![PathBuf::from("result.txt")];
    config.cancel.cancel();

    let provider = ScriptedProvider::new(Vec::new());
    let mut sink = MemorySink::default();

    let code = run(config, &provider, &mut sink).await.unwrap();
    assert_eq!(code, 0);
    // No model call, no turn, no outputs_validation.
    assert_eq!(provider.requests().len(), 0);
    assert_eq!(
        event_types(&sink),
        vec!["session", "agent_start", "agent_end"]
    );
    match sink.events.last().unwrap() {
        Event::AgentEnd(event) => assert_eq!(event.reason, Some(EndReason::Cancelled)),
        other => panic!("expected agent_end event, got {other:?}"),
    }
}

#[tokio::test]
async fn exhausted_budget_grants_one_wrap_up_turn_then_budget_exceeded() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("prompt.md"), "Work.\n").unwrap();
    let mut config = base_config(dir.path().canonicalize().unwrap());
    config.tools = vec![ToolKind::Read];
    config.budget = Budget::new(Some(1), None, Duration::from_secs(3600));
    config.session = Some(SessionLog::open(&dir.path().join("session")).unwrap());

    // Turn 1 (toolUse) completes within budget; the check before turn 2
    // fires and injects the single wrap-up notice.
    let provider = ScriptedProvider::new(vec![
        assistant_tool_call("call-0-0", "read", serde_json::json!({"path": "prompt.md"})),
        assistant_text("wrapping up", StopReason::Stop),
    ]);
    let mut sink = MemorySink::default();

    let code = run(config, &provider, &mut sink).await.unwrap();
    assert_eq!(code, 0);
    assert_eq!(turn_starts(&sink), 2);
    match sink.events.last().unwrap() {
        Event::AgentEnd(event) => assert_eq!(event.reason, Some(EndReason::BudgetExceeded)),
        other => panic!("expected agent_end event, got {other:?}"),
    }

    // The wrap-up notice was mirrored into the session log as a user
    // message between the tool result and the final assistant turn.
    let messages = session_messages(dir.path());
    assert_eq!(messages.len(), 5);
    assert_eq!(messages[3].role, Role::User);
    match &messages[3].content[0] {
        ContentBlock::Text { text } => assert!(text.contains("--max-turns")),
        other => panic!("expected text block, got {other:?}"),
    }
}

#[tokio::test]
async fn missing_required_output_runs_one_remediation_turn_then_fails_exit() {
    let dir = tempfile::tempdir().unwrap();
    let mut config = base_config(dir.path().canonicalize().unwrap());
    config.require_output = vec![PathBuf::from("result.txt")];
    config.session = Some(SessionLog::open(&dir.path().join("session")).unwrap());

    // Neither turn writes the declared artifact.
    let provider = ScriptedProvider::new(vec![
        assistant_text("done without writing", StopReason::Stop),
        assistant_text("still nothing", StopReason::Stop),
    ]);
    let mut sink = MemorySink::default();

    let code = run(config, &provider, &mut sink).await.unwrap();
    // Output contract: an exit-0 run must never end with declared
    // artifacts still missing.
    assert_eq!(code, EXIT_MISSING_OUTPUTS);

    // Exactly one remediation turn ran; outputs_validation reports the
    // still-missing artifact by its CLI-relative name.
    assert_eq!(turn_starts(&sink), 2);
    let validations: Vec<&OutputsValidationEvent> = sink
        .events
        .iter()
        .filter_map(|event| match event {
            Event::OutputsValidation(event) => Some(event),
            _ => None,
        })
        .collect();
    assert_eq!(validations.len(), 1);
    assert_eq!(validations[0].missing, vec!["result.txt"]);

    // The remediation notice was mirrored into the session log.
    let messages = session_messages(dir.path());
    assert_eq!(messages.len(), 4);
    assert_eq!(messages[2].role, Role::User);
    match &messages[2].content[0] {
        ContentBlock::Text { text } => {
            assert!(text.contains("SYSTEM NOTICE"));
            assert!(text.contains("result.txt"));
        }
        other => panic!("expected text block, got {other:?}"),
    }
}

#[tokio::test]
async fn remediation_turn_writing_the_artifact_ends_exit_zero() {
    let dir = tempfile::tempdir().unwrap();
    let mut config = base_config(dir.path().canonicalize().unwrap());
    config.tools = vec![ToolKind::Write];
    config.require_output = vec![PathBuf::from("result.txt")];

    let provider = ScriptedProvider::new(vec![
        assistant_text("done without writing", StopReason::Stop),
        assistant_tool_call(
            "call-0-0",
            "write",
            serde_json::json!({"path": "result.txt", "content": "payload"}),
        ),
        assistant_text("written", StopReason::Stop),
    ]);
    let mut sink = MemorySink::default();

    let code = run(config, &provider, &mut sink).await.unwrap();
    assert_eq!(code, 0);
    assert_eq!(
        std::fs::read_to_string(dir.path().join("result.txt")).unwrap(),
        "payload"
    );

    // The validation event is still emitted (with an empty missing list)
    // so the Host sees the final state explicitly.
    let validations: Vec<&OutputsValidationEvent> = sink
        .events
        .iter()
        .filter_map(|event| match event {
            Event::OutputsValidation(event) => Some(event),
            _ => None,
        })
        .collect();
    assert_eq!(validations.len(), 1);
    assert!(validations[0].missing.is_empty());
}
