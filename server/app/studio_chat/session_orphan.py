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
    no-runtime case (event + snapshot + structured 409, #1095). The timeline
    event is appended only while the row still carries this request's own
    stamp (status error AND the orphan detail, one atomic predicate), so a
    resume claiming the row after the recheck — or a further generation that
    then failed and stamped its own detail — never inherits a stale orphan
    error event.
    """
    status = str(session["status"])
    if status == "starting":
        raise ConflictError("Chat session is not running on this server")
    if status in _LIVE_STATUSES and service.db.update_studio_chat_session_if(
        session_id, status_in=(status,), status="error", error_detail=ORPHAN_ERROR_DETAIL
    ):
        rt = service.runtime(session_id)
        if rt is not None and _roll_back_under_live_generation(service, session_id, rt, session):
            raise ConflictError("Chat session was resumed concurrently; retry")
        # No live generation behind the stamp: either none was registered, or
        # the one the recheck saw exited before its lock was taken (#1095) —
        # its on_exit skipped the projection because the row already carried
        # this stamp, so this request owns the event + snapshot.
        # The event rides the #915 atomic live-guarded append, pinned to this
        # request's own stamp (status error + orphan detail): a resume that
        # claimed the row (error -> starting) — even one whose new runtime
        # then failed back to error with its own detail, already projected by
        # its on_error — makes the insert a no-op, and one racing it waits on
        # the FOR SHARE lock, so a stale orphan error never lands after (or
        # is pushed into) a resumed session.
        if service.store.append_message_if_live(
            session_id,
            "status",
            "system",
            {"event": "error", "detail": ORPHAN_ERROR_DETAIL},
            ("error",),
            error_detail_is=ORPHAN_ERROR_DETAIL,
        ):
            service.store.publish_session(session_id)
    raise StudioChatSessionInterruptedError(session_id)


def _roll_back_under_live_generation(
    service: StudioChatService,
    session_id: str,
    rt: SessionRuntime,
    session: dict[str, Any],
) -> bool:
    """Undo this request's orphan stamp while ``rt`` is still the registered,
    open generation, under its lock (on_exit holds it end to end), and report
    whether it was — the caller then answers a retry conflict. The undo is
    pinned by ``error_detail_is``, so a real failure that generation's
    on_error wrote over the stamp is never rolled back. ``False`` means the
    generation exited/was replaced before the lock was taken: the stamp (if
    still there) is this request's to project, which the caller's
    stamp-pinned atomic append decides.
    """
    with rt.lock:
        if service.runtime(session_id) is not rt or rt.closed:
            return False
        service.db.update_studio_chat_session_if(
            session_id,
            status_in=("error",),
            error_detail_is=ORPHAN_ERROR_DETAIL,
            status=str(session["status"]),
            error_detail=session.get("error_detail") or "",
        )
        return True
