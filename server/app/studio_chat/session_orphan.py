"""Orphaned-session projection on send (#760).

The DB row is the durable status, the runtime registry is process-local: a
row still saying idle/running/awaiting_permission while this process holds
no runtime is an orphan (the startup reap is the primary repair; this is the
backstop for any path that leaves one behind). Sending to it can never work,
so the send both lands the row on ``error`` — the frontend's resume bar keys
off it — and answers a structured 409 that points at resume instead of a
bare "not running" string.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Never

from server.app.services.job_errors import ConflictError

if TYPE_CHECKING:
    from server.app.studio_chat.service import StudioChatService

SESSION_INTERRUPTED_CODE = "studio_chat_session_interrupted"
ORPHAN_ERROR_DETAIL = "agent runtime is no longer running on this server"
_LIVE_STATUSES = ("idle", "running", "awaiting_permission")


class StudioChatSessionInterruptedError(ConflictError):
    """409 whose detail carries ``{message, code, session_id}`` (the
    DraftConflictError payload channel of routes/job_http.py)."""

    def __init__(self, session_id: str) -> None:
        self.payload: dict[str, Any] = {
            # Keeps the legacy "not running on this server" wording so
            # existing matchers still read it; the resume hint is the new part.
            "message": "Chat session was interrupted (its agent is not running"
            " on this server); resume the session to continue",
            "code": SESSION_INTERRUPTED_CODE,
            "session_id": session_id,
        }
        super().__init__(self.payload["message"])


def reject_orphaned_session(
    service: StudioChatService, session_id: str, session: dict[str, Any]
) -> Never:
    """Project the orphan to ``error`` and refuse the request (send, or a
    mode/config switch — anything that needs the live runtime).

    ``starting`` is not an orphan: create/resume hold the row there while the
    runtime is still being registered, so it keeps the plain refusal. The
    status write is a compare-and-set on the status this caller observed
    (#158 guarded update), so a close, an error transition or a resume that
    moved the row in between is never overwritten. A runtime registered in
    the window between the absence check and the write (resume racing this
    request) rolls the write back to the observed status instead of
    stamping a live session error.
    """
    status = str(session["status"])
    if status == "starting":
        raise ConflictError("Chat session is not running on this server")
    if status in _LIVE_STATUSES and service.db.update_studio_chat_session_if(
        session_id, status_in=(status,), status="error", error_detail=ORPHAN_ERROR_DETAIL
    ):
        if service.runtime(session_id) is not None:
            service.db.update_studio_chat_session_if(
                session_id,
                status_in=("error",),
                status=status,
                error_detail=session.get("error_detail") or "",
            )
            raise ConflictError("Chat session was resumed concurrently; retry")
        service.store.append_message(
            session_id, "status", "system", {"event": "error", "detail": ORPHAN_ERROR_DETAIL}
        )
        service.store.publish_session(session_id)
    raise StudioChatSessionInterruptedError(session_id)
