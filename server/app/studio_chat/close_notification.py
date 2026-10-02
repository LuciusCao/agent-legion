"""Best-effort terminal timeline projection after producers have been fenced."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.app.studio_chat.events import ServiceBackend

logger = logging.getLogger(__name__)


def notify_closed(backend: ServiceBackend, session_id: str, pending: list[str]) -> None:
    try:
        for request_id in pending:
            backend.store.append_message(
                session_id,
                "permission",
                "user",
                {"request_id": request_id, "status": "resolved", "decision": {"deny": True}},
            )
        backend.store.append_message(session_id, "status", "system", {"event": "session_closed"})
        backend.store.publish_session(session_id)
    except Exception:
        # #204 broad-except audit: closure already committed; failed notification
        # cannot imply retry or skip teardown. REST recovers the durable state.
        logger.warning("closed chat notification failed for %s", session_id, exc_info=True)
