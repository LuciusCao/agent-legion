//! The agent loop (design §4/§5).
//!
//! Context assembly is fully explicit — `--system-prompt` + `--skill`
//! SKILL.md contents + the instruction (with `@file` expansion). There is NO
//! auto-discovery anywhere in this binary: no AGENTS.md reading, no directory
//! scanning, no user-level config (zero-auto-discovery invariant).
//!
//! Loop: emit events, call the provider, execute tool calls, append results,
//! repeat until `stop` / `error` / budget exhausted. Model-call failures end
//! the run with `stopReason=error` + `errorMessage` on the last assistant
//! message and exit code 0 (Pi semantics; the Host judges failure from the
//! event stream, not the exit code).

use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::events::{
    AgentEndEvent, AgentStartEvent, Event, EventSink, Message, MessageEndEvent, MessageStartEvent,
    Role, SessionEvent, StopReason, ToolExecutionEndEvent, ToolExecutionStartEvent, ToolResultData,
    TurnEndEvent, TurnStartEvent, Usage,
};
use crate::provider::{CompletionRequest, Provider, ToolSpec};
use crate::session::SessionLog;
use crate::tools::{ToolContext, ToolKind, ToolOutput};

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
    pub max_turns: Option<u32>,
    pub max_tokens: Option<u64>,
    /// Parsed in M1; enforcement semantics land in M3.
    pub require_output: Vec<PathBuf>,
    pub session: Option<SessionLog>,
    /// Canonicalized working directory = tool sandbox root.
    pub cwd: PathBuf,
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

    let tool_specs: Vec<ToolSpec> = config.tools.iter().map(|kind| kind.spec()).collect();
    let tool_ctx = ToolContext {
        cwd: config.cwd.clone(),
    };

    let mut messages = vec![Message::user(config.instruction.clone())];
    append_session(&mut config.session, messages.last().expect("just pushed"))?;

    let mut turn_index: u32 = 0;
    let mut total_tokens: u64 = 0;
    let mut final_error: Option<String> = None;

    loop {
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
        let message = match provider.complete(&request).await {
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

        match stop_reason {
            Some(StopReason::ToolUse) => {
                // M1 budget handling: basic counting only; exceeding the
                // budget simply ends the loop (graceful wrap-up semantics
                // land in M3).
                if config.max_turns.is_some_and(|max| turn_index >= max)
                    || config.max_tokens.is_some_and(|max| total_tokens >= max)
                {
                    break;
                }
            }
            Some(StopReason::Error) => {
                final_error = messages.last().and_then(|msg| msg.error_message.clone());
                break;
            }
            _ => break,
        }
    }

    sink.emit(&Event::AgentEnd(AgentEndEvent {
        messages,
        error: final_error,
    }));
    Ok(0)
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
