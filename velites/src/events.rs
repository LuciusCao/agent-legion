//! Event schema (velites/json1): the pi-compatible subset emitted on stdout.
//!
//! The wire format mirrors the Pi `--mode json` events that Agent Legion
//! consumes:
//!
//! - `shared/pi_events.py` — event allowlist for compression;
//! - `server/app/services/token_usage.py` — `message_end.message.usage`
//!   (`input`/`output`/`cacheRead`), `provider`, `model`;
//! - `shared/pi_model_error.py` — `stopReason` + `errorMessage`
//!   failure semantics;
//! - `server/app/services/job_log_renderer.py` — UI preview over
//!   `agent_start` / `turn_start` / `message_end` (roles `assistant` and
//!   `toolResult`, content blocks `text` / `thinking` / `toolCall`).
//!
//! Delta events (`message_update`, `tool_execution_update`) intentionally do
//! NOT exist here — removing them is the core motivation of velites (they are
//! 99%+ of Pi's stdout volume and are always discarded downstream).

use std::io::Write;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

/// Stop reason of an assistant message. Wire names are camelCase to match Pi
/// (`stop` / `length` / `toolUse` / `error` / `aborted`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "camelCase")]
pub enum StopReason {
    Stop,
    Length,
    ToolUse,
    Error,
    Aborted,
}

/// Message role. `ToolResult` serializes as `toolResult` (pi-compatible).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "camelCase")]
pub enum Role {
    User,
    Assistant,
    ToolResult,
}

/// Token usage of one assistant message. `cacheRead` is camelCase on the wire.
///
/// Pi-aligned semantics: `input` EXCLUDES cache-read tokens (the provider's
/// `prompt_tokens` includes them; subtracting avoids double-billing the
/// cached part), so `input + cacheRead` reconstructs `prompt_tokens`.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "camelCase")]
pub struct Usage {
    /// Non-cached input tokens (pi-aligned; excludes `cacheRead`).
    pub input: u64,
    pub output: u64,
    pub cache_read: u64,
}

/// Request-level timing of one LLM completion (velites extension; Pi has no
/// equivalent). Attached to the assistant message of the SUCCESSFUL attempt —
/// failed transient attempts surface as pi-compatible error `message_end`
/// events without timing. All durations are wall-clock milliseconds measured
/// by the provider:
///
/// - `ttfbMs`: POST sent → first SSE `data:` chunk (for the non-streaming
///   JSON fallback dialect: → response headers);
/// - `streamMs`: first → last SSE `data:` chunk (`[DONE]` or connection end);
/// - `totalMs`: POST sent → stream end.
///
/// Tokens-per-second is NOT stored: consumers derive it as
/// `usage.output / (streamMs / 1000)`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "camelCase")]
pub struct RequestTiming {
    /// POST sent → first response chunk, in milliseconds.
    pub ttfb_ms: u64,
    /// First → last stream chunk, in milliseconds.
    pub stream_ms: u64,
    /// POST sent → stream end, in milliseconds.
    pub total_ms: u64,
}

/// Content block of a message (pi-compatible shapes).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "type")]
pub enum ContentBlock {
    #[serde(rename = "text")]
    Text { text: String },
    #[serde(rename = "thinking")]
    Thinking { thinking: String },
    #[serde(rename = "toolCall")]
    ToolCall {
        /// Filled by the provider (stub auto-generates `call-<n>-<i>`).
        #[serde(default)]
        id: String,
        name: String,
        arguments: serde_json::Value,
    },
}

/// One conversation message. Assistant metadata (`usage`, `provider`, `model`,
/// `stopReason`, `errorMessage`, `timing`) and tool-result metadata (`toolCallId`,
/// `toolName`, `isError`) are only present on the roles they apply to; `None`
/// fields are skipped on the wire.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "camelCase")]
pub struct Message {
    pub role: Role,
    pub content: Vec<ContentBlock>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub usage: Option<Usage>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub provider: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stop_reason: Option<StopReason>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error_message: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub is_error: Option<bool>,
    /// Request-level timing (velites extension); only set by real providers
    /// on successful completions — the stub provider and error events omit it.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub timing: Option<RequestTiming>,
}

impl Message {
    /// A message with no metadata besides role + content.
    pub fn bare(role: Role, content: Vec<ContentBlock>) -> Self {
        Self {
            role,
            content,
            usage: None,
            provider: None,
            model: None,
            stop_reason: None,
            error_message: None,
            tool_call_id: None,
            tool_name: None,
            is_error: None,
            timing: None,
        }
    }

    pub fn user(text: String) -> Self {
        Self::bare(Role::User, vec![ContentBlock::Text { text }])
    }

    pub fn tool_result(
        tool_call_id: String,
        tool_name: String,
        content: Vec<ContentBlock>,
        is_error: bool,
    ) -> Self {
        let mut msg = Self::bare(Role::ToolResult, content);
        msg.tool_call_id = Some(tool_call_id);
        msg.tool_name = Some(tool_name);
        msg.is_error = Some(is_error);
        msg
    }
}

/// `session` event: first line of the stream.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "camelCase")]
pub struct SessionEvent {
    /// Session identifier (`--name`, or a generated fallback).
    pub session_id: String,
    /// Unix seconds at session start.
    pub timestamp: u64,
}

/// `agent_start` event: emitted once before the first turn.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct AgentStartEvent {}

/// Why the run ended early. Absent on a normal completion or an unrecovered
/// model error (the `error` field covers that path); present only when the
/// harness itself cut the run short (design §5 controllability).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum EndReason {
    /// A run budget (turns / tokens / wall-clock deadline) was exhausted;
    /// the model got one wrap-up turn before the loop ended.
    BudgetExceeded,
    /// SIGTERM requested cancellation; the loop stopped at the next
    /// checkpoint (turn boundary or after the current tool execution).
    Cancelled,
}

/// `agent_end` event: final line of the stream. `error` is present only when
/// the run ended with an unrecovered model error (exit code stays 0, matching
/// Pi; the Host judges failure from the event stream). `reason` is present
/// only on budget exhaustion or cancellation (exit code also stays 0 — see
/// `cancel.rs`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct AgentEndEvent {
    pub messages: Vec<Message>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<EndReason>,
}

/// `turn_start` event.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "camelCase")]
pub struct TurnStartEvent {
    /// 1-based turn counter.
    pub turn_index: u32,
}

/// `turn_end` event: assistant message plus the tool results it triggered.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "camelCase")]
pub struct TurnEndEvent {
    pub turn_index: u32,
    pub message: Message,
    pub tool_results: Vec<Message>,
}

/// `message_start` event: assistant message skeleton (role/provider/model
/// known, content empty).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct MessageStartEvent {
    pub message: Message,
}

/// `message_end` event: the final assistant message. This is the token-metering
/// and failure-detection record — `usage`, `provider`, `model`, `stopReason`,
/// `content`, `errorMessage` all live here.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct MessageEndEvent {
    pub message: Message,
}

/// `auto_retry_start` event (pi-compatible): emitted right after the error
/// `message_end` of a failed transient attempt, before the backoff sleep.
/// There is intentionally no `auto_retry_end` — Pi doesn't have one either;
/// the Host's `fold_model_error` treats the next successful assistant
/// `message_end` (`stopReason=stop|toolUse`) as "recovered".
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "camelCase")]
pub struct AutoRetryStartEvent {
    /// 1-based retry counter (pi wire field).
    pub attempt: u32,
    /// Total attempts allowed, initial call included.
    pub max_attempts: u32,
    /// Backoff delay before the retry fires, in milliseconds.
    pub delay_ms: u64,
    /// Short description of the transient error that triggered the retry.
    pub error: String,
}

/// The pi-compatible event pair for one failed transient attempt: an
/// assistant `message_end` (`stopReason=error` + `errorMessage`, zero usage)
/// followed by `auto_retry_start`. Mirrors what Node Pi emits on a retry
/// (see `tests/workflows/test_pi_protocol.py` "error recovered by retry"),
/// so the Host failure detection needs zero changes.
///
/// No `message_start` skeleton precedes the pair: the agent loop already
/// emitted one for the turn, and every Host consumer (`fold_model_error`,
/// `token_usage`, `job_log_renderer`) reads only `message_end` — the pi
/// fixture shows the retry pair without an intermediate `message_start`.
pub fn retry_attempt_events(
    provider: &str,
    model: &str,
    attempt: u32,
    max_attempts: u32,
    delay_ms: u64,
    error: &str,
) -> [Event; 2] {
    let mut message = Message::bare(Role::Assistant, Vec::new());
    message.usage = Some(Usage::default());
    message.provider = Some(provider.to_string());
    message.model = Some(model.to_string());
    message.stop_reason = Some(StopReason::Error);
    message.error_message = Some(error.to_string());
    [
        Event::MessageEnd(MessageEndEvent { message }),
        Event::AutoRetryStart(AutoRetryStartEvent {
            attempt,
            max_attempts,
            delay_ms,
            error: error.to_string(),
        }),
    ]
}

/// `tool_execution_start` event.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ToolExecutionStartEvent {
    #[serde(rename = "toolCallId")]
    pub tool_call_id: String,
    #[serde(rename = "toolName")]
    pub tool_name: String,
    pub args: serde_json::Value,
}

/// Result payload shared by `tool_execution_end` (`result.content`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ToolResultData {
    pub content: Vec<ContentBlock>,
}

/// `tool_execution_end` event. `output_bytes` measures the tool's output
/// volume (stdout+stderr for bash, written/returned bytes for write/read);
/// it is a measurement field only — no truncation happens in M1.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ToolExecutionEndEvent {
    #[serde(rename = "toolCallId")]
    pub tool_call_id: String,
    #[serde(rename = "toolName")]
    pub tool_name: String,
    pub result: ToolResultData,
    #[serde(rename = "isError")]
    pub is_error: bool,
    pub output_bytes: u64,
}

/// `outputs_validation` event (velites extension, design §5 输出自检):
/// emitted right before `agent_end` whenever `--require-output` was given and
/// the run ended normally or by budget exhaustion — always emitted in that
/// case so the Host can decide explicitly, with `missing` listing the
/// declared artifacts (relative paths as passed on the CLI) that still do not
/// exist after the single remediation turn. Not emitted on cancellation or
/// unrecovered model error.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct OutputsValidationEvent {
    pub missing: Vec<String>,
}

/// The velites/json1 event stream: exactly these eleven event types, NDJSON
/// on stdout, one JSON object per line.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Event {
    Session(SessionEvent),
    AgentStart(AgentStartEvent),
    AgentEnd(AgentEndEvent),
    TurnStart(TurnStartEvent),
    TurnEnd(TurnEndEvent),
    MessageStart(MessageStartEvent),
    MessageEnd(MessageEndEvent),
    AutoRetryStart(AutoRetryStartEvent),
    ToolExecutionStart(ToolExecutionStartEvent),
    ToolExecutionEnd(ToolExecutionEndEvent),
    OutputsValidation(OutputsValidationEvent),
}

/// Export the JSON Schema for the event stream (used by the `velites-schema`
/// bin and the schema currency test).
pub fn schema_json() -> String {
    let schema = schemars::schema_for!(Event);
    serde_json::to_string_pretty(&schema).expect("schema serialization cannot fail")
}

/// Sink for emitted events. Implementations must not fail the agent loop.
pub trait EventSink: Send {
    fn emit(&mut self, event: &Event);
}

/// Emits events as compact NDJSON on stdout (the worker pipes this into
/// `events.jsonl`).
pub struct StdoutJsonlSink;

impl StdoutJsonlSink {
    pub fn new() -> Self {
        Self
    }
}

impl Default for StdoutJsonlSink {
    fn default() -> Self {
        Self::new()
    }
}

impl EventSink for StdoutJsonlSink {
    fn emit(&mut self, event: &Event) {
        let line = serde_json::to_string(event).expect("event serialization cannot fail");
        let stdout = std::io::stdout();
        let mut lock = stdout.lock();
        let _ = lock.write_all(line.as_bytes());
        let _ = lock.write_all(b"\n");
        let _ = lock.flush();
    }
}

/// In-memory sink for tests.
#[derive(Debug, Default)]
pub struct MemorySink {
    pub events: Vec<Event>,
}

impl EventSink for MemorySink {
    fn emit(&mut self, event: &Event) {
        self.events.push(event.clone());
    }
}

/// Shared in-memory sink (tests): one handle drives the agent loop, a clone
/// is moved into the retry callback, and all events land in one ordered vec.
#[derive(Debug, Clone, Default)]
pub struct SharedMemorySink {
    pub events: std::sync::Arc<std::sync::Mutex<Vec<Event>>>,
}

impl EventSink for SharedMemorySink {
    fn emit(&mut self, event: &Event) {
        self.events
            .lock()
            .expect("sink poisoned")
            .push(event.clone());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn message_end_wire_shape_matches_pi_contract() {
        let mut msg = Message::bare(
            Role::Assistant,
            vec![
                ContentBlock::Thinking {
                    thinking: "hmm".into(),
                },
                ContentBlock::Text { text: "hi".into() },
                ContentBlock::ToolCall {
                    id: "call-1".into(),
                    name: "read".into(),
                    arguments: serde_json::json!({"path": "a.txt"}),
                },
            ],
        );
        msg.usage = Some(Usage {
            input: 10,
            output: 5,
            cache_read: 2,
        });
        msg.provider = Some("stub".into());
        msg.model = Some("stub".into());
        msg.stop_reason = Some(StopReason::ToolUse);
        let event = Event::MessageEnd(MessageEndEvent { message: msg });
        let value = serde_json::to_value(&event).unwrap();
        assert_eq!(value["type"], "message_end");
        assert_eq!(value["message"]["usage"]["input"], 10);
        assert_eq!(value["message"]["usage"]["output"], 5);
        assert_eq!(value["message"]["usage"]["cacheRead"], 2);
        assert_eq!(value["message"]["provider"], "stub");
        assert_eq!(value["message"]["model"], "stub");
        assert_eq!(value["message"]["stopReason"], "toolUse");
        assert_eq!(value["message"]["content"][2]["type"], "toolCall");
        // errorMessage must be skipped when None.
        assert!(value["message"].get("errorMessage").is_none());
    }

    #[test]
    fn retry_attempt_events_match_pi_retry_pattern() {
        // Pi pattern: error message_end, then auto_retry_start (see
        // tests/workflows/test_pi_protocol.py "error recovered by retry").
        let [error_end, retry_start] =
            retry_attempt_events("gateway", "kimi-k2.6", 1, 4, 2000, "terminated");
        let error_end = serde_json::to_value(&error_end).unwrap();
        assert_eq!(error_end["type"], "message_end");
        assert_eq!(error_end["message"]["role"], "assistant");
        assert_eq!(error_end["message"]["stopReason"], "error");
        assert_eq!(error_end["message"]["errorMessage"], "terminated");
        assert_eq!(error_end["message"]["provider"], "gateway");
        assert_eq!(error_end["message"]["model"], "kimi-k2.6");
        assert_eq!(error_end["message"]["usage"]["input"], 0);

        let retry_start = serde_json::to_value(&retry_start).unwrap();
        assert_eq!(retry_start["type"], "auto_retry_start");
        assert_eq!(retry_start["attempt"], 1);
        assert_eq!(retry_start["maxAttempts"], 4);
        assert_eq!(retry_start["delayMs"], 2000);
        assert_eq!(retry_start["error"], "terminated");
    }

    #[test]
    fn timing_wire_shape_and_skip_when_absent() {
        let mut msg = Message::bare(
            Role::Assistant,
            vec![ContentBlock::Text { text: "hi".into() }],
        );
        // Absent timing (stub provider, error events) is skipped, not null.
        let value = serde_json::to_value(&msg).unwrap();
        assert!(value.get("timing").is_none());

        msg.timing = Some(RequestTiming {
            ttfb_ms: 120,
            stream_ms: 480,
            total_ms: 600,
        });
        let value = serde_json::to_value(&msg).unwrap();
        assert_eq!(value["timing"]["ttfbMs"], 120);
        assert_eq!(value["timing"]["streamMs"], 480);
        assert_eq!(value["timing"]["totalMs"], 600);
        // Round-trip through the schema types.
        let decoded: Message = serde_json::from_value(value).unwrap();
        assert_eq!(decoded.timing, msg.timing);
    }

    #[test]
    fn stop_reason_wire_names() {
        let cases = [
            (StopReason::Stop, "stop"),
            (StopReason::Length, "length"),
            (StopReason::ToolUse, "toolUse"),
            (StopReason::Error, "error"),
            (StopReason::Aborted, "aborted"),
        ];
        for (reason, wire) in cases {
            assert_eq!(serde_json::to_value(reason).unwrap(), wire);
        }
    }

    #[test]
    fn tool_execution_end_wire_shape() {
        let event = Event::ToolExecutionEnd(ToolExecutionEndEvent {
            tool_call_id: "call-1".into(),
            tool_name: "bash".into(),
            result: ToolResultData {
                content: vec![ContentBlock::Text { text: "ok".into() }],
            },
            is_error: false,
            output_bytes: 2,
        });
        let value = serde_json::to_value(&event).unwrap();
        assert_eq!(value["type"], "tool_execution_end");
        assert_eq!(value["toolCallId"], "call-1");
        assert_eq!(value["toolName"], "bash");
        assert_eq!(value["result"]["content"][0]["text"], "ok");
        assert_eq!(value["isError"], false);
        assert_eq!(value["output_bytes"], 2);
    }

    #[test]
    fn agent_end_reason_and_outputs_validation_wire_shape() {
        let event = Event::AgentEnd(AgentEndEvent {
            messages: Vec::new(),
            error: None,
            reason: Some(EndReason::BudgetExceeded),
        });
        let value = serde_json::to_value(&event).unwrap();
        assert_eq!(value["type"], "agent_end");
        assert_eq!(value["reason"], "budget_exceeded");
        assert!(value.get("error").is_none());

        let event = Event::AgentEnd(AgentEndEvent {
            messages: Vec::new(),
            error: None,
            reason: Some(EndReason::Cancelled),
        });
        assert_eq!(serde_json::to_value(&event).unwrap()["reason"], "cancelled");

        // Normal end: reason is skipped, not null.
        let event = Event::AgentEnd(AgentEndEvent {
            messages: Vec::new(),
            error: None,
            reason: None,
        });
        assert!(serde_json::to_value(&event)
            .unwrap()
            .get("reason")
            .is_none());

        let event = Event::OutputsValidation(OutputsValidationEvent {
            missing: vec!["out/result.json".into()],
        });
        let value = serde_json::to_value(&event).unwrap();
        assert_eq!(value["type"], "outputs_validation");
        assert_eq!(value["missing"][0], "out/result.json");
    }

    #[test]
    fn event_type_tags_match_pi_allowlist() {
        // Must match RELEVANT_EVENT_TYPES in shared/pi_events.py.
        let tags = [
            "session",
            "agent_start",
            "agent_end",
            "turn_start",
            "turn_end",
            "message_start",
            "message_end",
            "auto_retry_start",
            "tool_execution_start",
            "tool_execution_end",
            "outputs_validation",
        ];
        let schema = serde_json::to_value(schemars::schema_for!(Event)).unwrap();
        for tag in tags {
            let found = schema.to_string().contains(&format!("\"{tag}\""));
            assert!(found, "schema missing tag {tag}");
        }
        // Delta events must never appear.
        assert!(!schema.to_string().contains("message_update"));
        assert!(!schema.to_string().contains("tool_execution_update"));
    }
}
