"""Generation-owned exit projection before registry removal; cleanup survives I/O faults."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.app.studio_chat.events import ServiceBackend
    from server.app.studio_chat.runtime import SessionRuntime


def finish_session_exit(
    backend: ServiceBackend,
    session_id: str,
    *,
    close_initiated: bool,
    expected: SessionRuntime | None,
) -> None:
    # Keep the generation registered until its last event is written.
    # Otherwise close could observe absence and publish its terminal
    # marker before this exiting thread appends a late error.
    runtime = expected or backend.runtime(session_id)
    if runtime is None:
        return
    with runtime.lock:
        try:
            if close_initiated or runtime.closed or backend.runtime(session_id) is not runtime:
                return
            changed = backend.db.update_studio_chat_session_if(
                session_id,
                status_not_in=("closed", "error", "starting"),
                status="error",
                error_detail="agent process exited",
            )
            if changed:
                backend.store.append_message(
                    session_id,
                    "status",
                    "system",
                    {"event": "error", "detail": "agent process exited"},
                )
                backend.store.publish_session(session_id)
        finally:
            # No join on the ACP thread; teardown always cleans this
            # generation, including failures in its final DB projection.
            backend.teardown_runtime(session_id, runtime, close_handle=False, expected=runtime)
