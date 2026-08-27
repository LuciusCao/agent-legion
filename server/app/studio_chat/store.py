"""Persistence + SSE publish layer for Studio chat sessions (issue #196).

Every durable write and every bus publish goes through this store so the
service facade stays a coordinator, and the permission/mcp_hint helpers
(stop reaching into service privates) get a narrow, explicit interface:
``append_message`` / ``publish_session`` / ``mark_mcp_verified`` /
``runtime``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from server.app.events.bus import EventBus
from server.app.jobs import JobQueries
from server.app.studio_chat.channels import studio_chat_channel
from server.app.studio_chat.payloads import (
    serialize_message,
    serialize_session,
)
from server.app.studio_chat.runtime import SessionRuntime
from server.app.studio_chat.streaming import stream_message_payload

logger = logging.getLogger(__name__)


class StudioChatStore:
    """DB writes + event-bus publishes for one StudioChatService."""

    def __init__(self, job_db: JobQueries, bus: EventBus | None) -> None:
        self._db = job_db
        self._bus = bus

    def append_message(
        self, session_id: str, kind: str, role: str, content: dict[str, Any]
    ) -> dict[str, Any]:
        message = self._db.append_studio_chat_message(session_id, kind, role, content)
        self.publish(session_id, {"type": "message", "message": serialize_message(message)})
        return message

    def publish_session(self, session_id: str) -> None:
        session = self._db.get_studio_chat_session(session_id)
        if session is not None:
            self.publish(session_id, {"type": "session", "session": serialize_session(session)})

    def publish(self, session_id: str, payload: dict[str, Any]) -> None:
        if self._bus is None:
            return
        payload = {"session_id": session_id, **payload}
        try:
            self._bus.publish(studio_chat_channel(session_id), json.dumps(payload, default=str))
        except Exception:
            logger.warning("failed to publish studio chat event for %s", session_id)

    def mark_mcp_verified(self, session_id: str) -> None:
        session = self._db.get_studio_chat_session(session_id) or {}
        if session.get("mcp_status") == "verified":
            return
        self._db.update_studio_chat_session(session_id, mcp_status="verified")

    def append_stream_chunk(
        self, session_id: str, runtime: SessionRuntime, kind: str, text: str
    ) -> None:
        """Fold a streamed chunk into the turn's single message of its kind."""
        if not text:
            return
        with runtime.lock:
            # The first-chunk create+attach stays in one critical section: a
            # turn-start reset landing between them would leave a stale open
            # id whose next chunk would overwrite the previous turn's
            # message row in place (#98).
            # Maintenance constraint: this section covers one DB INSERT plus
            # a bus.publish (append_message). It is safe today only because
            # EventBus.publish is non-blocking and takes no other lock — do
            # NOT add blocking calls (network, subprocess, additional locks)
            # here; every streaming chunk of every session on this runtime
            # serializes through this lock.
            open_id, full_text = runtime.stream.append(kind, text)
            if open_id is None:
                message = self.append_message(session_id, kind, "agent", {"text": full_text})
                runtime.stream.attach(kind, message["id"])
                return
        self._db.update_studio_chat_message_content(open_id, {"text": full_text})
        self.publish(
            session_id,
            {
                "type": "message",
                "message": stream_message_payload(session_id, open_id, kind, full_text),
            },
        )
