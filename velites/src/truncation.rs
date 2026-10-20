//! #865 truncation continuation (design §4 方案A): the pieces of the agent
//! loop's answer to a length-truncated reply (`stopReason=length`, per-call
//! output cap hit). A truncation is NOT a normal stop: below
//! [`MAX_CONSECUTIVE_LENGTH_STOPS`] consecutive truncations the loop injects
//! a continuation turn ([`continuation_message`]) after rewriting the
//! model-facing copy of the truncated message ([`sanitize_truncated_history`]
//! ); at the cap the run falls back to the pre-#865 ending (outputs
//! remediation / normal stop). Continuation turns get no extra budget — the
//! top-of-loop budget check gates every model call, continuation included.

use crate::events::{ContentBlock, Message, StopReason};

/// How many CONSECUTIVE per-call truncations still earn a continuation turn;
/// reaching the cap falls back to the pre-#865 ending. A non-length stop
/// resets the streak.
pub(crate) const MAX_CONSECUTIVE_LENGTH_STOPS: u32 = 2;

/// Observe one turn's stop reason on the truncation streak. A `length` stop
/// leaves one stderr trace (turn + output tokens + streak), sanitizes the
/// model-facing copy of the truncated message (any further request —
/// continuation, remediation, or budget wrap-up — would otherwise carry
/// un-executed tool calls the providers reject), and bumps the streak; any
/// other stop resets it. Returns the updated streak.
pub(crate) fn track_stop(
    stop_reason: Option<StopReason>,
    streak: u32,
    turn_index: u32,
    message: &mut Message,
) -> u32 {
    if stop_reason != Some(StopReason::Length) {
        return 0;
    }
    let streak = streak + 1;
    let output_tokens = message
        .usage
        .as_ref()
        .map(|usage| usage.output)
        .unwrap_or(0);
    eprintln!(
        "velites: model output hit the per-call output token limit (turn {turn_index}, \
         {output_tokens} output tokens, consecutive {streak}/{MAX_CONSECUTIVE_LENGTH_STOPS})"
    );
    sanitize_truncated_history(message);
    streak
}

/// Strip the un-executed tool calls of a length-truncated assistant message
/// from the model-facing history: Anthropic and OpenAI both reject a
/// `tool_use` without its `tool_result`, and a stream cut off mid-thinking
/// leaves a trailing Anthropic thinking block without its `signature`
/// (equally rejected on resend). Only the history copy sent to the provider
/// is rewritten; the session mirror and event stream keep the original.
pub(crate) fn sanitize_truncated_history(message: &mut Message) {
    message
        .content
        .retain(|block| !matches!(block, ContentBlock::ToolCall { .. }));
    let signed_thinking_left = message
        .provider_data
        .as_mut()
        .and_then(|data| data.get_mut("anthropicThinkingBlocks"))
        .and_then(serde_json::Value::as_array_mut)
        .map(|blocks| {
            blocks.retain(
                |block| match block.get("type").and_then(serde_json::Value::as_str) {
                    Some("thinking") => block
                        .get("signature")
                        .and_then(serde_json::Value::as_str)
                        .is_some_and(|signature| !signature.is_empty()),
                    Some("redacted_thinking") => block
                        .get("data")
                        .and_then(serde_json::Value::as_str)
                        .is_some_and(|data| !data.is_empty()),
                    _ => false,
                },
            );
            !blocks.is_empty()
        })
        .unwrap_or(false);
    if message.provider_data.is_some() && !signed_thinking_left {
        message.provider_data = None;
    }
    // A cut mid-tool-call can leave no visible content at all; an empty
    // assistant content array is rejected (Anthropic), so leave an explicit
    // marker instead.
    let has_text = message
        .content
        .iter()
        .any(|block| matches!(block, ContentBlock::Text { text } if !text.is_empty()));
    if !has_text && message.provider_data.is_none() {
        message.content.push(ContentBlock::Text {
            text: "(reply cut off by the per-call output token limit before any visible content)"
                .into(),
        });
    }
}

/// The user-message notice injected for a continuation turn: explains the
/// truncation, that its tool calls never ran, and how to continue (shorter
/// thinking, smaller write chunks, no rewriting of what is already on disk).
pub(crate) fn continuation_message() -> String {
    "SYSTEM NOTICE: your previous reply hit the per-call output token limit and \
     was cut off; the tool calls in it were NOT executed. Continue where you \
     stopped: keep thinking short, write the remaining artifacts in smaller \
     chunks across several tool calls, and do not rewrite content that is \
     already on disk."
        .into()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::events::Role;

    #[test]
    fn sanitize_strips_tool_calls_and_unsigned_thinking() {
        let mut message = Message::bare(
            Role::Assistant,
            vec![
                ContentBlock::Thinking {
                    thinking: "hmm".into(),
                },
                ContentBlock::Text {
                    text: "partial answer".into(),
                },
                ContentBlock::ToolCall {
                    id: "call-0-2".into(),
                    name: "write".into(),
                    arguments: serde_json::json!({"path": "out.txt", "content": "half"}),
                },
            ],
        );
        message.provider_data = Some(serde_json::json!({
            "anthropicThinkingBlocks": [
                {"type": "thinking", "thinking": "complete", "signature": "sig-1"},
                {"type": "thinking", "thinking": "cut off mid-block", "signature": ""},
                {"type": "redacted_thinking", "data": "blob"},
            ],
        }));

        sanitize_truncated_history(&mut message);

        assert!(
            !message
                .content
                .iter()
                .any(|block| matches!(block, ContentBlock::ToolCall { .. })),
            "un-executed tool calls must not reach the next request"
        );
        assert!(message
            .content
            .iter()
            .any(|block| matches!(block, ContentBlock::Text { text } if text == "partial answer")));
        let blocks = message.provider_data.as_ref().unwrap()["anthropicThinkingBlocks"]
            .as_array()
            .unwrap()
            .clone();
        // The unsigned trailing thinking block is dropped; the signed one and
        // the redacted block (data-carried, no signature) survive.
        assert_eq!(blocks.len(), 2);
        assert_eq!(blocks[0]["signature"], "sig-1");
        assert_eq!(blocks[1]["type"], "redacted_thinking");
    }

    #[test]
    fn sanitize_marks_contentless_messages() {
        // Cut off mid-tool-call: nothing visible survives the strip, and an
        // empty assistant content array would be rejected (Anthropic) — a
        // marker text block takes its place.
        let mut message = Message::bare(
            Role::Assistant,
            vec![ContentBlock::ToolCall {
                id: "call-0-0".into(),
                name: "write".into(),
                arguments: serde_json::json!({"path": "out.txt"}),
            }],
        );
        message.provider_data = Some(serde_json::json!({
            "anthropicThinkingBlocks": [
                {"type": "thinking", "thinking": "cut", "signature": ""},
            ],
        }));

        sanitize_truncated_history(&mut message);

        assert!(message.provider_data.is_none());
        assert_eq!(message.content.len(), 1);
        match &message.content[0] {
            ContentBlock::Text { text } => assert!(text.contains("cut off")),
            other => panic!("expected marker text block, got {other:?}"),
        }
    }

    #[test]
    fn continuation_message_covers_the_contract() {
        let message = continuation_message();
        assert!(message.starts_with("SYSTEM NOTICE"));
        assert!(message.contains("NOT executed"));
        assert!(message.contains("smaller chunks"));
        assert!(message.contains("already on disk"));
    }
}
