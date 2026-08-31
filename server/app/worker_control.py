"""Public worker pause/resume control: the backend-selection seam (issue 287).

Production resolves to the persisted backend (``worker_control_db.py``);
the in-memory legacy mode lives in ``worker_control_memory.py``. The public
name every consumer imports stays here, so the split changes no call site.
"""

from __future__ import annotations

from server.app.db.dialect import ConnectSource
from server.app.worker_control_db import PersistedWorkspaceWorkerControl
from server.app.worker_control_memory import InMemoryWorkspaceWorkerControl


class WorkspaceWorkerControl:
    # ``db_path`` accepts the JobQueries facade or a bare DSN string
    # (BOUNDARY-DATA-001, #187), handed to the persisted backend untouched;
    # None keeps the in-memory legacy mode.
    def __init__(
        self, db_path: ConnectSource | None = None, *, process_id: str | None = None
    ) -> None:
        self._backend = (
            InMemoryWorkspaceWorkerControl()
            if db_path is None
            else PersistedWorkspaceWorkerControl(db_path, process_id)
        )

    def pause(self, workspace_id: str) -> None:
        self._backend.pause(workspace_id)

    def resume(self, workspace_id: str) -> None:
        self._backend.resume(workspace_id)

    def is_paused(self, workspace_id: str) -> bool:
        return self._backend.is_paused(workspace_id)

    # Startup reset is policy, not storage: resume state must not survive a
    # restart — both backends implement it, the app calls it once at startup.
    def reset_all_to_paused(self) -> None:
        self._backend.reset_all_to_paused()
