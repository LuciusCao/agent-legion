"""Resume transcript rebuild and one-shot marker choreography (studio chat).

When a resumed agent cannot reload its prior ACP session (loadSession not
advertised, or the load failed), the service prepends this transcript to the
first post-resume user prompt so the fresh agent regains the conversation
context. Source of truth is the persisted studio_chat_messages timeline —
only user/agent text participates; tool calls, plans and status rows are
noise for context rebuild. Preparation is read-only; prompt admission consumes
the one-shot marker only after the input has been durably accepted.
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
    before_seq: int | None = None,
) -> tuple[str, bool]:
    """Prepare a prompt without consuming the one-shot resume marker.

    Acceptance consumes the marker, including for a first prompt that uses
    the authoring bootstrap. Preparation failures leave it untouched.
    With no watermark, read existing history before the new message exists.
    """
    with runtime.lock:
        pending = runtime.resume_transcript_pending
    if not pending or first_prompt:
        return prompt_text, pending
    transcript = build_resume_transcript(
        db.list_studio_chat_messages_tail(session_id, before_seq=before_seq)
    )
    if not transcript:
        return prompt_text, pending
    return transcript + prompt_text, pending
