"""Generation-pinned closure; notification failure cannot skip owned teardown."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from server.app.studio_chat.close_notification import notify_closed

if TYPE_CHECKING:
    from server.app.studio_chat.service import StudioChatService


def close_session(
    service: StudioChatService,
    session_id: str,
    workspace_id: str,
    *,
    include_deleted: bool = False,
) -> dict[str, Any]:
    # include_deleted: only the soft-delete path (#872) closes a row it has
    # already stamped; every public caller keeps the stamped-row 404.
    session = service.get_session(session_id, workspace_id, include_deleted=include_deleted)
    if session["status"] == "closed":
        return session
    runtime = service.runtime(session_id)
    committed = False
    try:
        with runtime.lock if runtime is not None else nullcontext():
            with service._runtimes_lock:
                # Pin absence too: resumed runtimes registered after the
                # snapshot must not inherit this stale close's DB write.
                current = service._runtimes.get(session_id)
                if current is not None and current is not runtime:
                    return service.get_session(session_id, include_deleted=include_deleted)
                service.db.update_studio_chat_session(
                    session_id, status="closed", closed_at=datetime.now(UTC)
                )
            committed = True
            # Fence producers before the terminal marker. Teardown performs
            # blocking handle cleanup outside this lock, but callbacks and
            # the watcher already see this generation as retired.
            if runtime is not None:
                runtime.closed = True
            pending = list(runtime.pending_permissions) if runtime is not None else []
            if runtime is not None:
                service._settle_pending_permissions(runtime)
            notify_closed(service, session_id, pending)
    finally:
        if committed and runtime is not None:
            service.teardown_runtime(session_id, runtime, expected=runtime)
    return service.get_session(session_id, include_deleted=include_deleted)


# Bound on close retries after a visibility stamp (delete #872 / archive #924).
_SETTLE_ATTEMPTS = 3


def close_until_settled(
    service: StudioChatService, session_id: str, workspace_id: str, *, include_deleted: bool
) -> None:
    """Close after a stamp that the resume claim refuses (deleted_at /
    archived_at): a resume that claimed before the stamp can still be
    mid-spawn, and close's generation pin bails when a runtime registered
    after its snapshot, so retry (bounded) until the row is closed with no
    runtime registered. A spawn whose row was closed or stamped under it
    tears itself down at the registration fence / readiness check (spawn.py),
    so the bound only has to cover the registration window."""
    for _ in range(_SETTLE_ATTEMPTS):
        session = close_session(service, session_id, workspace_id, include_deleted=include_deleted)
        if session["status"] == "closed" and service.runtime(session_id) is None:
            return
