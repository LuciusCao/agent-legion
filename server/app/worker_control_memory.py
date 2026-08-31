"""In-memory worker pause control: the pre-PostgreSQL legacy mode.

Kept as its own module because it is a different storage backend, not a
different policy: the contract (unknown workspaces default to paused,
resume never surviving a restart) is identical to the persisted
``WorkspaceWorkerControl`` in ``worker_control.py``, which delegates here
when constructed without ``db_path``. Production always passes the
JobQueries facade (see ``main.py``); this module only serves tests and any
embedding that has no database at all.
"""

from __future__ import annotations

import threading


class InMemoryWorkspaceWorkerControl:
    """Per-workspace pause/resume control with process-local state only."""

    def __init__(self) -> None:
        self._paused: dict[str, bool] = {}
        self._lock = threading.Lock()

    def pause(self, workspace_id: str) -> None:
        with self._lock:
            self._paused[workspace_id] = True

    def resume(self, workspace_id: str) -> None:
        with self._lock:
            self._paused[workspace_id] = False

    def is_paused(self, workspace_id: str) -> bool:
        with self._lock:
            # Unknown workspaces default to paused (fail-closed dispatch).
            return self._paused.get(workspace_id, True)

    def reset_all_to_paused(self) -> None:
        """Clear every scope: unknown workspaces read as paused afterwards."""
        with self._lock:
            self._paused.clear()
