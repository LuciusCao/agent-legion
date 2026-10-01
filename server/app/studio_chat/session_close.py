"""Generation-pinned closure; notification failure cannot skip owned teardown."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from server.app.studio_chat.service import StudioChatService


def close_session(service: StudioChatService, session_id: str, workspace_id: str) -> dict[str, Any]:
    session = service.get_session(session_id, workspace_id)
    if session["status"] == "closed":
        return session
    runtime = service.runtime(session_id)
    try:
        with runtime.lock if runtime is not None else nullcontext():
            if runtime is not None and service.runtime(session_id) is not runtime:
                return service.get_session(session_id)
            service.db.update_studio_chat_session(
                session_id, status="closed", closed_at=datetime.now(UTC)
            )
            service.store.append_message(
                session_id, "status", "system", {"event": "session_closed"}
            )
            service.store.publish_session(session_id)
    finally:
        if runtime is not None:
            service.teardown_runtime(session_id, runtime, expected=runtime)
    return service.get_session(session_id)
