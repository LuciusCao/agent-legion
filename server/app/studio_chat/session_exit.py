"""Generation-owned exit projection before registry removal; cleanup survives I/O faults."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.app.studio_chat.events import ServiceBackend
    from server.app.studio_chat.runtime import SessionRuntime
from server.app.studio_chat.exit_settlement import project_process_exit


def finish_session_exit(
    backend: ServiceBackend,
    session_id: str,
    *,
    close_initiated: bool,
    expected: SessionRuntime | None,
    project_only: bool = False,
) -> None:
    runtime = expected or backend.runtime(session_id)
    if runtime is None:
        return
    try:
        if not close_initiated:
            # Keep the generation registered through its durable projection.
            # The helper fences this generation before its first DB write and
            # supports idempotent retries after the registry entry is gone.
            project_process_exit(backend, session_id, runtime)
    finally:
        # No runtime lock is held here, and close_handle=False avoids a
        # self-join on the ACP thread. A stale generation cleans only itself.
        # The early EOF projection keeps registration so resume can still join
        # a live direct child; thread-final on_exit performs the actual removal.
        if not project_only:
            backend.teardown_runtime(session_id, runtime, close_handle=False, expected=runtime)
