"""Resume transcript rebuild and one-shot marker choreography (studio chat).

When a resumed agent cannot reload its prior ACP session (loadSession not
advertised, or the load failed), the service prepends this transcript to the
first post-resume user prompt so the fresh agent regains the conversation
context. Source of truth is the persisted studio_chat_messages timeline —
only user/agent text participates; tool calls, plans and status rows are
noise for context rebuild. Replaces transcript.py (file budget): the marker
consume/re-arm helpers moved in next to the transcript builder they feed.
"""

from __future__ import annotations

from typing import Any

from server.app.jobs import JobQueries
from server.app.studio_chat.runtime import SessionRuntime

# Truncation budget: the transcript is prompt payload, so cap it well below
# any model's context window — the most recent exchanges matter most.
RESUME_TRANSCRIPT_MAX_CHARS = 6000

# Marks a hard-cut single entry (one message alone overflowing the budget).
RESUME_TRANSCRIPT_OMITTED = "[…前文省略]"

RESUME_TRANSCRIPT_HEADER = (
    "\n\n---\n[系统注入] 以下是此前对话的记录（会话中断后恢复，供你回顾上下文；"
    "不要重复已经完成的工作）：\n"
)
RESUME_TRANSCRIPT_FOOTER = "[此前对话记录结束。以下是用户的新消息：]\n\n"


def build_resume_transcript(messages: list[dict[str, Any]]) -> str:
    """Rebuild a compact user/assistant transcript; "" when nothing usable."""
    entries: list[str] = []
    for message in messages:
        if message.get("kind") != "text" or message.get("role") not in ("user", "agent"):
            continue
        speaker = "用户" if message["role"] == "user" else "助手"
        text = str((message.get("content") or {}).get("text") or "")
        if text:
            entries.append(f"{speaker}：{text}")
    transcript = ""
    for entry in reversed(entries):
        candidate = f"{entry}\n{transcript}" if transcript else entry
        if len(candidate) > RESUME_TRANSCRIPT_MAX_CHARS:
            if transcript:
                break
            # A single entry overflowing the budget on its own must not wave
            # through whole: hard-cut it (tail kept — the recent end matters
            # most) and stop; nothing older can fit afterwards.
            keep = RESUME_TRANSCRIPT_MAX_CHARS - len(RESUME_TRANSCRIPT_OMITTED)
            candidate = RESUME_TRANSCRIPT_OMITTED + candidate[-keep:]
        transcript = candidate
    if not transcript:
        return ""
    return RESUME_TRANSCRIPT_HEADER + transcript + RESUME_TRANSCRIPT_FOOTER


def prepare_resume_prompt(
    runtime: SessionRuntime,
    db: JobQueries,
    session_id: str,
    first_prompt: bool,
    prompt_text: str,
    before_seq: int,
) -> tuple[str, bool]:
    """Consume the one-shot resume marker; prepend the transcript when due.

    The marker is consumed unconditionally — a first-prompt turn takes the
    authoring bootstrap instead and must not leak the marker into the next
    turn (it would inject the session's own fresh conversation as "resumed"
    context). The transcript is built only when the session actually has
    history, and only from messages before ``before_seq``: the just-appended
    user message is the prompt tail, not context, and must not appear twice.
    Returns the (possibly rewritten) prompt plus whether the marker had been
    set, so the caller can re-arm it when the prompt never reaches the agent.
    """
    with runtime.lock:
        pending = runtime.resume_transcript_pending
        runtime.resume_transcript_pending = False
    if not pending or first_prompt:
        return prompt_text, pending
    transcript = build_resume_transcript(
        db.list_studio_chat_messages_tail(session_id, before_seq=before_seq)
    )
    if not transcript:
        return prompt_text, pending
    return transcript + prompt_text, pending


def rearm_resume_transcript(runtime: SessionRuntime, was_pending: bool) -> None:
    """Restore the one-shot marker after a send_prompt failure (nothing was
    injected, so re-arming carries no double-injection risk)."""
    if not was_pending:
        return
    with runtime.lock:
        runtime.resume_transcript_pending = True
