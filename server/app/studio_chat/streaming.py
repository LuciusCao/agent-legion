"""Per-turn streaming chunk coalescing for Studio chat (agent text + thought).

ACP streams one ``agent_message_chunk`` / ``agent_thought_chunk`` per fragment;
each kind folds into one persisted row per uninterrupted stretch. A non-text
update (tool call, plan, permission prompt) interrupts the prose: the service
calls ``reset()`` so the next chunk opens a FRESH row below the interruption
instead of folding the whole turn's text above every tool card (#98 follow-up).
Trailing chunks of a finished turn can arrive after turn end (the ACP SDK
resolves the prompt response before the last update handlers run); they fold
into still-open rows, or seed one tail row when the turn ended on an
interruption — matching their actual position. Slots are never reset at turn
end itself, only at the START of the next turn or mid-turn on interruption:
a turn-end reset would let a trailing chunk start a tail-only orphan row, and
a slot attached after a reset would make the next turn's first chunk
overwrite the previous turn's row in place (#98).

Residual window: chunks carry no turn id, so a trailing chunk arriving after
the next turn's reset seeds a tail-only orphan row (no turn-tagged chunks).

The state lives on SessionRuntime under the runtime lock; first-chunk
create+attach is one critical section so a reset never lands between them.
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

    def reset(self) -> None:
        # Drop the open slots: called at the start of a turn, and mid-turn
        # when a non-text update (tool call / plan / permission prompt)
        # interrupts the prose so following chunks open fresh rows.
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
