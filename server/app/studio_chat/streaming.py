"""Per-turn streaming chunk coalescing for Studio chat (agent text + thought).

ACP streams one ``agent_message_chunk`` / ``agent_thought_chunk`` notification
per fragment; each kind folds into a single persisted message row per turn
(create on first chunk, in-place content updates after). The ACP SDK resolves
the prompt response before the last session/update handler tasks run, so
buffer-until-turn-end would lose trailing chunks — open rows tolerate any
ordering, and turn end closes the open slots.

The state lives on SessionRuntime and is guarded by the runtime lock.
"""

from __future__ import annotations

from typing import Any


class TurnStreamState:
    """Open per-kind streaming message slots for the current turn."""

    def __init__(self) -> None:
        self.message_ids: dict[str, str] = {}
        self.texts: dict[str, str] = {}

    def append(self, kind: str, chunk: str) -> tuple[str | None, str]:
        """Merge a chunk; returns (open message id or None, full text)."""
        full_text = self.texts.get(kind, "") + chunk
        self.texts[kind] = full_text
        return self.message_ids.get(kind), full_text

    def attach(self, kind: str, message_id: str) -> None:
        self.message_ids[kind] = message_id

    def close(self) -> None:
        self.message_ids.clear()
        self.texts.clear()


def stream_message_payload(
    session_id: str, message_id: str, kind: str, text: str
) -> dict[str, Any]:
    """SSE message payload for an in-place streaming content update (no seq)."""
    return {
        "id": message_id,
        "session_id": session_id,
        "kind": kind,
        "role": "agent",
        "content": {"text": text},
    }
