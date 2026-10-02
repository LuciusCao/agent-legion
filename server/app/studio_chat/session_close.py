"""Generation-pinned closure; notification failure cannot skip owned teardown."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from server.app.studio_chat.close_notification import notify_closed

if TYPE_CHECKING:
    from server.app.studio_chat.service import StudioChatService


def close_session(service: StudioChatService, session_id: str, workspace_id: str) -> dict[str, Any]:
    session = service.get_session(session_id, workspace_id)
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
                    return service.get_session(session_id)
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
    return service.get_session(session_id)
