from __future__ import annotations

import os
import socket
import threading
from datetime import UTC, datetime

from server.app.db.retry import retried_on_database_conflict
from server.app.db.transaction import read_connection, write_transaction


@retried_on_database_conflict
def _persist_pause(db_path: str, scope: str, paused: bool, process_id: str) -> None:
    now = datetime.now(UTC)
    with write_transaction(db_path) as conn:
        conn.execute(
            "insert into worker_control_state (scope, paused, updated_by, updated_at)"
            " values (?, ?, ?, ?) on conflict(scope) do update set paused = excluded.paused,"
            " updated_by = excluded.updated_by, updated_at = excluded.updated_at",
            (scope, int(paused), process_id, now),
        )


class WorkspaceWorkerControl:
    """Per-workspace worker pause/resume control. With ``db_path`` the state
    persists in ``worker_control_state`` across processes/restarts; without it
    the control is in-memory only. Unknown workspaces default to paused."""

    def __init__(self, db_path: str | None = None, *, process_id: str | None = None) -> None:
        self._db_path = db_path
        self._process_id = process_id or f"{socket.gethostname()}:{os.getpid()}"
        self._paused: dict[str, bool] = {}
        self._lock = threading.Lock()

    def pause(self, workspace_id: str) -> None:
        self._set(workspace_id, True)

    def resume(self, workspace_id: str) -> None:
        self._set(workspace_id, False)

    def is_paused(self, workspace_id: str) -> bool:
        if self._db_path is None:
            with self._lock:
                return self._paused.get(workspace_id, True)
        with read_connection(self._db_path) as conn:
            row = conn.execute(
                "select paused from worker_control_state where scope = ?",
                (f"workspace:{workspace_id}",),
            ).fetchone()
        return True if row is None else bool(row["paused"])

    def _set(self, workspace_id: str, paused: bool) -> None:
        if self._db_path is None:
            with self._lock:
                self._paused[workspace_id] = paused
            return
        _persist_pause(self._db_path, f"workspace:{workspace_id}", paused, self._process_id)
