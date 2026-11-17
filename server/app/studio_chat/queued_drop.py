"""Durable unstarted-drop helpers for queued human input."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from server.app.studio_chat.payloads import serialize_message

if TYPE_CHECKING:
    from server.app.studio_chat.runtime import SessionRuntime
    from server.app.studio_chat.service import StudioChatService
    from server.app.studio_chat.turn_lease import TurnLease

logger = logging.getLogger(__name__)


def publish_committed(service: StudioChatService, session_id: str, row: dict[str, Any]) -> None:
    """Push an already-committed status row + snapshot; best-effort only."""
    try:
        service.store.publish(session_id, {"type": "message", "message": serialize_message(row)})
        service.store.publish_session(session_id)
    except Exception:
        # #204 broad-except audit: the row and claim are durable; a failed
        # publish must not abort the claimed turn's prompt. Traceback retained.
        logger.warning("queued chat message publish failed for %s", session_id, exc_info=True)


def _note_dropped(
    service: StudioChatService,
    session_id: str,
    message_id: str,
    detail: str,
    *,
    runtime: SessionRuntime | None = None,
    require_durable: bool = False,
) -> bool:
    try:
        service.store.append_message(
            session_id,
            "status",
            "system",
            {"event": "queued_dropped", "message_id": message_id, "detail": detail},
        )
        service.store.publish_session(session_id)
    except Exception:
        # #204 broad-except audit: strict FIFO release needs a durable row;
        # legacy unprompted callers keep their advisory fallback. Traceback retained.
        logger.warning("queued chat message notice failed for %s", session_id, exc_info=True)
        if runtime is not None and not require_durable:
            runtime.inbound_messages.pop(message_id, None)
        return False
    if runtime is not None:
        runtime.inbound_messages.pop(message_id, None)
    return True


def drop_unstarted(
    service: StudioChatService,
    session_id: str,
    runtime: SessionRuntime,
    message_id: str,
    detail: str,
    owner: TurnLease,
    fatal_detail: str,
) -> bool:
    if _note_dropped(
        service, session_id, message_id, detail, runtime=runtime, require_durable=True
    ):
        owner.abort()
    else:
        owner.fail_claim_uncertain(fatal_detail)
    return False
