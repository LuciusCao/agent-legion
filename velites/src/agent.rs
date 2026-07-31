//! The agent loop (design §4/§5).
//!
//! Context assembly is fully explicit — `--system-prompt` + `--skill`
//! SKILL.md contents + the instruction (with `@file` expansion). There is NO
//! auto-discovery anywhere in this binary: no AGENTS.md reading, no directory
//! scanning, no user-level config (zero-auto-discovery invariant).
//!
//! Loop: emit events, call the provider, execute tool calls, append results,
//! repeat until `stop` / `error` / budget exhausted / cancelled. Model-call
//! failures end the run with `stopReason=error` + `errorMessage` on the last
//! assistant message and exit code 0 (Pi semantics; the Host judges failure
//! from the event stream, not the exit code).
//!
//! Controllability (design §5):
//!
//! - Budget (see `budget.rs`): checked before every model call; on
//!   exhaustion the model gets ONE wrap-up turn, then the run ends with
//!   `agent_end{reason: "budget_exceeded"}`.
//! - Cancellation (see `cancel.rs`): SIGTERM is checked at the turn
//!   boundary, during the model call, and around every tool execution; the
//!   run ends with `agent_end{reason: "cancelled"}` and exit 0.
//! - Output self-check (`--require-output`): before a normal ending the
//!   declared artifacts are checked; missing ones trigger ONE remediation
//!   turn, and an `outputs_validation{missing: [...]}` event is always
//!   emitted (on normal/budget endings) so the Host can decide explicitly.

use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use anyhow::anyhow;

use crate::budget::Budget;
use crate::cancel::CancelToken;
use crate::events::{
    AgentEndEvent, AgentStartEvent, EndReason, Event, EventSink, Message, MessageEndEvent,
    MessageStartEvent, OutputsValidationEvent, Role, SessionEvent, StopReason,
    ToolExecutionEndEvent, ToolExecutionStartEvent, ToolResultData, TurnEndEvent, TurnStartEvent,
    Usage,
};
use crate::provider::{CompletionRequest, Provider, ToolSpec};
use crate::session::SessionLog;
use crate::tools::{resolve_in_cwd, ToolContext, ToolKind, ToolOutput};

pub struct AgentConfig {
    /// Session identifier (`--name`); a pid-based fallback is generated.
    pub name: Option<String>,
    pub provider_name: String,
    pub model: String,
    pub thinking: Option<String>,
    /// Fully assembled system prompt (system-prompt flag + skill contents).
    pub system_prompt: String,
    /// Fully expanded instruction (`@file` already resolved).
    pub instruction: String,
    pub tools: Vec<ToolKind>,
    /// Run budgets (turns / tokens / wall-clock deadline), checked before
    /// every model call.
    pub budget: Budget,
    /// Declared artifacts that must exist when the run ends (raw CLI paths;
    /// sandbox-validated before the loop starts).
    pub require_output: Vec<PathBuf>,
    pub session: Option<SessionLog>,
    /// Canonicalized working directory = tool sandbox root.
    pub cwd: PathBuf,
    /// Cancellation flag (SIGTERM-driven in the binary; default/disarmed in
    /// library use).
    pub cancel: CancelToken,
}

/// Which wrap-up turn is in flight, if any. Exactly one wrap-up turn runs;
/// the run ends when it completes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum WrapUp {
    /// Budget exhausted: model writes out declared artifacts, then the run
    /// ends with `reason: budget_exceeded`.
    Budget,
    /// Declared outputs missing: model gets one remediation turn, then the
    /// run ends normally and `outputs_validation` reports the final state.
    Outputs,
}

/// A `--require-output` entry after sandbox validation.
struct RequiredOutput {
    /// The path as passed on the CLI (reported in `outputs_validation`).
    display: String,
    /// Sandbox-resolved absolute path (existence checks).
    resolved: PathBuf,
}

pub async fn run<P: Provider>(
    mut config: AgentConfig,
    provider: &P,
    sink: &mut dyn EventSink,
) -> anyhow::Result<u8> {
    let session_id = config
        .name
        .clone()
        .unwrap_or_else(|| format!("velites-{}", std::process::id()));
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);

    sink.emit(&Event::Session(SessionEvent {
        session_id,
        timestamp,
    }));
    sink.emit(&Event::AgentStart(AgentStartEvent {}));

    // An escaping --require-output path is a caller misconfiguration
    // (harness-side error, non-zero exit), never a runtime "missing" entry.
    let required_outputs = resolve_required_outputs(&config.require_output, &config.cwd)?;

    let tool_specs: Vec<ToolSpec> = config.tools.iter().map(|kind| kind.spec()).collect();
    let tool_ctx = ToolContext {
        cwd: config.cwd.clone(),
        cancel: config.cancel.clone(),
    };

    let mut messages = vec![Message::user(config.instruction.clone())];
    append_session(&mut config.session, messages.last().expect("just pushed"))?;

    let mut turn_index: u32 = 0;
    let mut total_tokens: u64 = 0;
    let mut final_error: Option<String> = None;
    let mut end_reason: Option<EndReason> = None;
    let mut wrap_up: Option<WrapUp> = None;
    // Set once the wrap-up turn has actually run; the top-of-loop end check
    // must not fire between injecting the wrap-up message and its turn.
    let mut wrap_up_turn_ran = false;

    loop {
        // Cancellation checkpoint: turn boundary (before the model call).
        if config.cancel.is_cancelled() {
            end_reason = Some(EndReason::Cancelled);
            break;
        }

        // The previous turn was the single wrap-up turn — the run ends now.
        if wrap_up_turn_ran {
            if matches!(wrap_up, Some(WrapUp::Budget)) {
                end_reason = Some(EndReason::BudgetExceeded);
            }
            emit_outputs_validation(sink, &required_outputs);
            break;
        }

        // Budget check BEFORE the next model call: on exhaustion inject one
        // wrap-up message and give the model a final turn to write out its
        // declared artifacts.
        if wrap_up.is_none() {
            if let Some(violation) = config.budget.exhausted(turn_index, total_tokens) {
                let notice = Message::user(crate::budget::wrap_up_message(violation));
                messages.push(notice.clone());
                append_session(&mut config.session, &notice)?;
                wrap_up = Some(WrapUp::Budget);
            }
        }

        turn_index += 1;
        sink.emit(&Event::TurnStart(TurnStartEvent { turn_index }));

        let mut skeleton = Message::bare(Role::Assistant, Vec::new());
        skeleton.provider = Some(config.provider_name.clone());
        skeleton.model = Some(config.model.clone());
        sink.emit(&Event::MessageStart(MessageStartEvent {
            message: skeleton,
        }));

        let request = CompletionRequest {
            model: &config.model,
            system: &config.system_prompt,
            messages: &messages,
            tools: &tool_specs,
            thinking: config.thinking.as_deref(),
        };
        let message = tokio::select! {
            result = provider.complete(&request) => match result {
                Ok(message) => message,
                Err(err) => {
                    // Unrecovered model failure: record it as the final assistant
                    // message (stopReason=error + errorMessage), keep exit 0.
                    let mut failed = Message::bare(Role::Assistant, Vec::new());
                    failed.usage = Some(Usage::default());
                    failed.provider = Some(config.provider_name.clone());
                    failed.model = Some(config.model.clone());
                    failed.stop_reason = Some(StopReason::Error);
                    failed.error_message = Some(err.to_string());
                    failed
                }
            },
            // Cancellation aborts an in-flight model call (dropping the
            // future cancels the HTTP request); the run ends at once.
            _ = config.cancel.wait() => {
                end_reason = Some(EndReason::Cancelled);
                break;
            }
        };

        if let Some(usage) = &message.usage {
            total_tokens =
                total_tokens.saturating_add(usage.input + usage.output + usage.cache_read);
        }
        let stop_reason = message.stop_reason;

        sink.emit(&Event::MessageEnd(MessageEndEvent {
            message: message.clone(),
        }));
        messages.push(message.clone());
        append_session(&mut config.session, &message)?;

        let mut tool_results: Vec<Message> = Vec::new();
        if stop_reason == Some(StopReason::ToolUse) {
            for block in &message.content {
                // Cancellation checkpoint: do not start further tool calls.
                if config.cancel.is_cancelled() {
                    break;
                }
                let crate::events::ContentBlock::ToolCall {
                    id,
                    name,
                    arguments,
                } = block
                else {
                    continue;
                };
                sink.emit(&Event::ToolExecutionStart(ToolExecutionStartEvent {
                    tool_call_id: id.clone(),
                    tool_name: name.clone(),
                    args: arguments.clone(),
                }));

                let output: ToolOutput = match config
                    .tools
                    .iter()
                    .copied()
                    .find(|kind| kind.name() == name)
                {
                    Some(kind) => kind.execute(arguments, &tool_ctx).await,
                    None => ToolOutput::error(format!(
                        "tool `{name}` is not enabled (enabled: {})",
                        enabled_tool_names(&config.tools)
                    )),
                };

                sink.emit(&Event::ToolExecutionEnd(ToolExecutionEndEvent {
                    tool_call_id: id.clone(),
                    tool_name: name.clone(),
                    result: ToolResultData {
                        content: output.content.clone(),
                    },
                    is_error: output.is_error,
                    output_bytes: output.output_bytes,
                }));

                let result_message =
                    Message::tool_result(id.clone(), name.clone(), output.content, output.is_error);
                messages.push(result_message.clone());
                append_session(&mut config.session, &result_message)?;
                tool_results.push(result_message);
            }
        }

        sink.emit(&Event::TurnEnd(TurnEndEvent {
            turn_index,
            message,
            tool_results,
        }));

        if wrap_up.is_some() {
            wrap_up_turn_ran = true;
        }

        match stop_reason {
            Some(StopReason::ToolUse) => {
                // Next iteration's top-of-loop checks (cancel / wrap-up end /
                // budget) decide whether another turn runs.
            }
            Some(StopReason::Error) => {
                final_error = messages.last().and_then(|msg| msg.error_message.clone());
                break;
            }
            _ => {
                // Normal stop. A finished wrap-up turn ends the run; the
                // budget wrap-up is what sets the reason.
                if wrap_up.is_some() {
                    if matches!(wrap_up, Some(WrapUp::Budget)) {
                        end_reason = Some(EndReason::BudgetExceeded);
                    }
                    emit_outputs_validation(sink, &required_outputs);
                    break;
                }
                // Output self-check: missing declared artifacts get exactly
                // one remediation turn.
                let missing = missing_outputs(&required_outputs);
                if !missing.is_empty() {
                    let notice = Message::user(outputs_remediation_message(&missing));
                    messages.push(notice.clone());
                    append_session(&mut config.session, &notice)?;
                    wrap_up = Some(WrapUp::Outputs);
                    continue;
                }
                emit_outputs_validation(sink, &required_outputs);
                break;
            }
        }
    }

    sink.emit(&Event::AgentEnd(AgentEndEvent {
        messages,
        error: final_error,
        reason: end_reason,
    }));
    Ok(0)
}

/// Validate `--require-output` paths against the cwd sandbox (same resolver
/// the tools use); escapes are a harness-side configuration error.
fn resolve_required_outputs(paths: &[PathBuf], cwd: &Path) -> anyhow::Result<Vec<RequiredOutput>> {
    paths
        .iter()
        .map(|path| {
            let display = path.to_string_lossy().into_owned();
            let resolved = resolve_in_cwd(cwd, &display)
                .map_err(|err| anyhow!("invalid --require-output `{display}`: {err}"))?;
            Ok(RequiredOutput { display, resolved })
        })
        .collect()
}

/// Declared artifacts that do not exist right now (CLI-relative display form).
fn missing_outputs(required: &[RequiredOutput]) -> Vec<String> {
    required
        .iter()
        .filter(|output| !output.resolved.exists())
        .map(|output| output.display.clone())
        .collect()
}

/// Always emit `outputs_validation` when `--require-output` was given (even
/// with an empty `missing` list) so the Host can decide explicitly.
fn emit_outputs_validation(sink: &mut dyn EventSink, required: &[RequiredOutput]) {
    if required.is_empty() {
        return;
    }
    sink.emit(&Event::OutputsValidation(OutputsValidationEvent {
        missing: missing_outputs(required),
    }));
}

fn outputs_remediation_message(missing: &[String]) -> String {
    format!(
        "SYSTEM NOTICE: the following declared output artifacts are missing: {}. \
         Write them out now, then stop.",
        missing.join(", ")
    )
}

fn append_session(session: &mut Option<SessionLog>, message: &Message) -> anyhow::Result<()> {
    if let Some(log) = session {
        log.append(message)?;
    }
    Ok(())
}

fn enabled_tool_names(tools: &[ToolKind]) -> String {
    tools
        .iter()
        .map(|kind| kind.name())
        .collect::<Vec<_>>()
        .join(",")
}
