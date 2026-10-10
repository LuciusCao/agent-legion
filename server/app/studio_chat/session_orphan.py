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

from typing import TYPE_CHECKING, Any, Literal, Never

from server.app.services.job_errors import ConflictError

if TYPE_CHECKING:
    from server.app.studio_chat.runtime import SessionRuntime
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
    stamping a live session error. The rollback is pinned to that runtime
    generation and to this write's own error stamp: it runs under the
    runtime's lock (on_exit holds it end to end) and only while that same
    runtime is still registered and open, so a new runtime that fails
    (on_error rewriting the detail) or exits (on_exit tearing down while the
    row still carries this stamp) is never rolled back to the stale observed
    status with no runtime behind it; such a gone generation leaves the stamp
    owned by this request, which then completes the projection like the
    no-runtime case (event + snapshot + structured 409, #1095). The timeline event is appended only
    while the row still says error (atomic predicate), so a resume claiming
    the row after the recheck never inherits a stale error event.
    """
    status = str(session["status"])
    if status == "starting":
        raise ConflictError("Chat session is not running on this server")
    if status in _LIVE_STATUSES and service.db.update_studio_chat_session_if(
        session_id, status_in=(status,), status="error", error_detail=ORPHAN_ERROR_DETAIL
    ):
        rt = service.runtime(session_id)
        verdict = (
            "owned" if rt is None else _settle_against_generation(service, session_id, rt, session)
        )
        if verdict == "live":
            raise ConflictError("Chat session was resumed concurrently; retry")
        if verdict == "superseded":
            raise StudioChatSessionInterruptedError(session_id)
        # No live generation behind the stamp: either none was registered, or
        # the one the recheck saw exited before its lock was taken (#1095) —
        # its on_exit skipped the projection because the row already carried
        # this stamp, so this request owns the event + snapshot.
        # The event rides the #915 atomic live-guarded append: a resume that
        # claimed the row (error -> starting) after the recheck above makes
        # the insert a no-op, and one racing it waits on the FOR SHARE lock
        # — a stale error never lands after (or is pushed into) a resumed
        # session.
        if service.store.append_message_if_live(
            session_id,
            "status",
            "system",
            {"event": "error", "detail": ORPHAN_ERROR_DETAIL},
            ("error",),
        ):
            service.store.publish_session(session_id)
    raise StudioChatSessionInterruptedError(session_id)


def _settle_against_generation(
    service: StudioChatService,
    session_id: str,
    rt: SessionRuntime,
    session: dict[str, Any],
) -> Literal["live", "owned", "superseded"]:
    """Decide the orphan stamp's fate against the generation the recheck saw,
    under its lock (on_exit holds it end to end, so a gone generation's own
    projection writes are complete once it is taken).

    ``live``: ``rt`` is still the registered, open generation — undo this
    request's own stamp (pinned by ``error_detail_is``, never a real failure
    rewritten over it) and let the caller answer a retry conflict.
    ``owned``: the generation exited/was replaced and the row still carries
    this request's stamp — nobody else projects it (on_exit skips an error
    row), so the caller appends the event and publishes the snapshot.
    ``superseded``: the row moved on (its on_error stamped a real failure and
    already appended/published, or a further resume claimed it) — the caller
    only answers the structured refusal.
    """
    with rt.lock:
        current = service.runtime(session_id)
        if current is rt and not rt.closed:
            service.db.update_studio_chat_session_if(
                session_id,
                status_in=("error",),
                error_detail_is=ORPHAN_ERROR_DETAIL,
                status=str(session["status"]),
                error_detail=session.get("error_detail") or "",
            )
            return "live"
        row = service.db.get_studio_chat_session(session_id) or {}
    owned = row.get("status") == "error" and row.get("error_detail") == ORPHAN_ERROR_DETAIL
    return "owned" if owned else "superseded"
