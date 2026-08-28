"""Resume transcript rebuild (studio chat).

When a resumed agent cannot reload its prior ACP session (loadSession not
advertised, or the load failed), the service prepends this transcript to the
first post-resume user prompt so the fresh agent regains the conversation
context. Source of truth is the persisted studio_chat_messages timeline —
only user/agent text participates; tool calls, plans and status rows are
noise for context rebuild.
"""

from __future__ import annotations

from typing import Any

# Truncation budget: the transcript is prompt payload, so cap it well below
# any model's context window — the most recent exchanges matter most.
RESUME_TRANSCRIPT_MAX_CHARS = 6000

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
        if transcript and len(candidate) > RESUME_TRANSCRIPT_MAX_CHARS:
            break
        transcript = candidate
    if not transcript:
        return ""
    return RESUME_TRANSCRIPT_HEADER + transcript + RESUME_TRANSCRIPT_FOOTER
