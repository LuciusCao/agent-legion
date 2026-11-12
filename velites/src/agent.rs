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
//!   #443 upgrades the check: when a `--skill` directory declares an output
//!   contract block (`references/output-contract.md`), the contract engine
//!   validates file contents too — `outputs_validation` then reports
//!   `mode: "contract"` with `violations`, and violations join missing
//!   artifacts in triggering the remediation turn and the exit-1 gate
//!   (contract parse errors fail closed as one violation).
//!   Artifacts still missing when a non-cancelled run ends fail the output
//!   contract: the process exits 1, so an exit-0 run with missing declared
//!   outputs never reaches the caller.
//! - Truncation continuation (#865, design §4 方案A, see `truncation.rs`): a
//!   length-truncated reply (`stopReason=length`, per-call output cap hit) is
//!   NOT a normal stop. Its un-executed tool calls (and unsigned Anthropic
//!   thinking blocks) are stripped from the model-facing history — the
//!   session mirror and event stream keep the original — a continuation user
//!   message is injected, and the loop continues within the SAME budgets.
//!   After `MAX_CONSECUTIVE_LENGTH_STOPS` consecutive truncations the run
//!   falls back to the pre-#865 ending (remediation turn / normal stop).

mod output;
mod tools;

use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use self::output::{emit_outputs_validation, missing_outputs, resolve_required_outputs};
use self::tools::enabled_tool_names;
use crate::budget::Budget;
use crate::cancel::CancelToken;
use crate::contract;
use crate::contract_gate::{gate_outcome, remediation_message};
use crate::events::{
    AgentEndEvent, AgentStartEvent, EndReason, Event, EventSink, Message, MessageEndEvent,
    MessageStartEvent, Role, SessionEvent, StopReason, ToolExecutionEndEvent,
    ToolExecutionStartEvent, ToolResultData, TurnEndEvent, TurnStartEvent, Usage,
};
use crate::provider::{CompletionRequest, Provider, ToolSpec};
use crate::session::SessionLog;
use crate::tools::{ToolContext, ToolKind, ToolOutput};
use crate::truncation::{continuation_message, track_stop, MAX_CONSECUTIVE_LENGTH_STOPS};

/// Exit code for a run that ended without its declared `--require-output`
/// artifacts (output contract violation). Harness faults exit 2 (see
/// main.rs); 0 keeps Pi semantics for everything else.
pub const EXIT_MISSING_OUTPUTS: u8 = 1;

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
    /// Canonicalized extra read-only roots for the `read` tool (`--skill`
    /// dirs + session dir, design §5). Writes never use these.
    pub read_roots: Vec<PathBuf>,
    /// Canonicalized `--skill` directories; the output-contract engine and
    /// the `validate` tool resolve the contract from the first one that
    /// declares a block (#443).
    pub skill_dirs: Vec<PathBuf>,
    /// OS-level filesystem sandbox for the `bash` tool (`None` = --no-sandbox).
    pub sandbox: Option<std::sync::Arc<crate::sandbox::Sandbox>>,
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
    /// Declared outputs missing or contract violated: model gets one
    /// remediation turn, then the run ends normally and `outputs_validation`
    /// reports the final state.
    Outputs,
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
    // #443: the first --skill directory declaring an output contract block
    // upgrades the end-of-run gate from existence to contract mode; a
    // malformed block fails closed (recorded as a violation at emit time).
    let contract = contract::first_contract(&config.skill_dirs);

    let tool_specs: Vec<ToolSpec> = config.tools.iter().map(|kind| kind.spec()).collect();
    let tool_ctx = ToolContext {
        cwd: config.cwd.clone(),
        cancel: config.cancel.clone(),
        sandbox: config.sandbox.clone(),
        read_roots: config.read_roots.clone(),
        skill_dirs: config.skill_dirs.clone(),
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
    // #865: consecutive length-truncated replies; reset by any other stop.
    let mut consecutive_lengths: u32 = 0;

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
            emit_outputs_validation(sink, &required_outputs, contract.as_ref(), &config.cwd);
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
                    // #469 phase decomposition; `None` on pre-measurement
                    // errors (disabled tool, validation failure), matching
                    // the RequestTiming convention on message_end.
                    timing: output.timing,
                }));

                let result_message =
                    Message::tool_result(id.clone(), name.clone(), output.content, output.is_error);
                messages.push(result_message.clone());
                append_session(&mut config.session, &result_message)?;
            }
        }

        sink.emit(&Event::TurnEnd(TurnEndEvent { turn_index }));

        if wrap_up.is_some() {
            wrap_up_turn_ran = true;
        }

        // #865: a length-truncated reply is not a normal stop — track_stop
        // traces it on stderr, sanitizes the model-facing copy, and counts
        // the consecutive streak; the match below decides between a
        // continuation turn and the pre-#865 ending. Session mirror and
        // event stream keep the ORIGINAL message.
        let stopped = messages.last_mut().expect("turn message just pushed");
        consecutive_lengths = track_stop(stop_reason, consecutive_lengths, turn_index, stopped);

        match stop_reason {
            Some(StopReason::ToolUse) => {
                // Next iteration's top-of-loop checks (cancel / wrap-up end /
                // budget) decide whether another turn runs.
            }
            Some(StopReason::Error) => {
                final_error = messages.last().and_then(|msg| msg.error_message.clone());
                break;
            }
            // #865 continuation turn: below the consecutive cap and outside a
            // wrap-up turn, a truncation injects a continuation notice (it
            // does NOT consume the WrapUp::Outputs remediation turn) and the
            // loop continues. At the cap — or when the wrap-up turn itself
            // truncated — the length stop falls into the normal-stop arm.
            Some(StopReason::Length)
                if wrap_up.is_none() && consecutive_lengths < MAX_CONSECUTIVE_LENGTH_STOPS =>
            {
                let notice = Message::user(continuation_message());
                messages.push(notice.clone());
                append_session(&mut config.session, &notice)?;
                continue;
            }
            _ => {
                // Normal stop — also the pre-#865 ending a length stop falls
                // back to at the continuation cap or on a wrap-up turn. A
                // finished wrap-up turn ends the run; the budget wrap-up is
                // what sets the reason.
                if wrap_up.is_some() {
                    if matches!(wrap_up, Some(WrapUp::Budget)) {
                        end_reason = Some(EndReason::BudgetExceeded);
                    }
                    emit_outputs_validation(
                        sink,
                        &required_outputs,
                        contract.as_ref(),
                        &config.cwd,
                    );
                    break;
                }
                // Output self-check: missing declared artifacts or contract
                // violations get exactly one remediation turn. The gate is
                // driven by --require-output: without declared artifacts
                // there is nothing to remediate against (the contract engine
                // still runs for the emitted event when artifacts exist).
                // A contract PARSE error lives in the read-only skill dir —
                // the model can never fix it, so the remediation turn would
                // be a wasted model call: skip straight to the failed exit.
                if !required_outputs.is_empty() && !matches!(contract, Some(Err(_))) {
                    let missing = missing_outputs(&required_outputs);
                    let (_, violations) = gate_outcome(contract.as_ref(), &config.cwd);
                    if !missing.is_empty() || !violations.is_empty() {
                        let notice = Message::user(remediation_message(&missing, &violations));
                        messages.push(notice.clone());
                        append_session(&mut config.session, &notice)?;
                        wrap_up = Some(WrapUp::Outputs);
                        continue;
                    }
                }
                emit_outputs_validation(sink, &required_outputs, contract.as_ref(), &config.cwd);
                break;
            }
        }
    }

    sink.emit(&Event::AgentEnd(AgentEndEvent {
        error: final_error,
        reason: end_reason,
    }));

    // Output contract at exit: a run that declared --require-output
    // artifacts and ends with them still missing — or with contract
    // violations outstanding — FAILED, whatever the loop ending looked like
    // (normal stop, budget exhaustion, unrecovered model error) — say so
    // with a non-zero exit code instead of forcing the caller to parse the
    // event stream (exit-0 "false completions").
    // Cancellation is exempt: it is a deliberate Host action, not a
    // failed run.
    if !required_outputs.is_empty() && !matches!(end_reason, Some(EndReason::Cancelled)) {
        let (_, violations) = gate_outcome(contract.as_ref(), &config.cwd);
        if !missing_outputs(&required_outputs).is_empty() || !violations.is_empty() {
            return Ok(EXIT_MISSING_OUTPUTS);
        }
    }
    Ok(0)
}

fn append_session(session: &mut Option<SessionLog>, message: &Message) -> anyhow::Result<()> {
    if let Some(log) = session {
        log.append(message)?;
    }
    Ok(())
}
