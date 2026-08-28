"""SSE channel naming for Studio chat sessions."""

from __future__ import annotations


def studio_chat_channel(session_id: str) -> str:
    return f"studio-chat:{session_id}"
