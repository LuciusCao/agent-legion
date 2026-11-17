"""FIFO admission for human messages behind a platform-initiated turn (#882).

Messages persist with ``content.queued`` and enter the handle's FIFO. Their
actual ``before_start`` claim lives in inbound_delivery.py: queued_delivered
means that claim committed, never that conn.prompt or the model completed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from server.app.auth.sessions import hash_token
from server.app.studio_chat.inbound_delivery import (
    DROPPED_COMPACTING,
    DROPPED_ERROR,
    _deliver,
    _note_dropped,
    note_enqueued,
    publish_committed,
)
from server.app.studio_chat.queued_admission import DELIVERED_DETAIL, DROPPED_BUSY, DROPPED_TOKEN

__all__ = [
    "DELIVERED_DETAIL",
    "DROPPED_BUSY",
    "DROPPED_COMPACTING",
    "DROPPED_ERROR",
    "DROPPED_TOKEN",
    "_deliver",
    "_note_dropped",
    "enqueue",
    "publish_committed",
    "should_queue",
]

if TYPE_CHECKING:
    from server.app.studio_chat.runtime import SessionRuntime
    from server.app.studio_chat.service import StudioChatService
    from server.app.studio_chat.turn_lease import TurnLease


def should_queue(runtime: SessionRuntime, status: str) -> bool:
    """Caller holds runtime.lock: preserve FIFO behind a turn or other input."""
    if runtime.inbound_pending > 0:
        return status in ("idle", "running", "awaiting_permission")
    return (
        status in ("running", "awaiting_permission")
        and runtime.turn_open
        and runtime.turn_background
    )


def enqueue(
    service: StudioChatService,
    session_id: str,
    runtime: SessionRuntime,
    text: str,
    prompt: str,
    commit_wakeup: Callable[[], None],
) -> dict[str, Any] | None:
    """Persist and hand off one message, or None when admission is fenced."""
    message: dict[str, Any] | None = None

    def accept() -> None:
        nonlocal message
        message = service.db.enqueue_studio_chat_message(
            session_id, hash_token(runtime.token), text
        )
        note_enqueued(runtime, message)
        runtime.resume_transcript_pending = False
        commit_wakeup()

    def before_start() -> TurnLease | bool:
        assert message is not None
        return _deliver(service, session_id, runtime, str(message["id"]), text, prompt)

    if not runtime.handle.send_prompt(prompt, accept=accept, before_start=before_start):
        return None
    return message
