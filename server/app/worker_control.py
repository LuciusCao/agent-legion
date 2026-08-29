from __future__ import annotations

import os
import socket
import threading
from datetime import UTC, datetime

from server.app.db.dialect import ConnectSource
from server.app.db.retry import with_database_conflict_retry
from server.app.db.transaction import read_connection, write_transaction


@with_database_conflict_retry
def _persist_pause(db_path: ConnectSource, scope: str, paused: bool, process_id: str) -> None:
    now = datetime.now(UTC)
    with write_transaction(db_path) as conn:
        conn.execute(
            "insert into worker_control_state (scope, paused, updated_by, updated_at)"
            " values (%s, %s, %s, %s) on conflict(scope) do update set paused = excluded.paused,"
            " updated_by = excluded.updated_by, updated_at = excluded.updated_at",
            (scope, int(paused), process_id, now),
        )


class WorkspaceWorkerControl:
    """Per-workspace worker pause/resume control. With ``db_path`` the state
    persists in ``worker_control_state`` so every control-plane process sees
    the same value; unknown workspaces default to paused. Resume state must
    NOT survive a restart — auto-dispatch coming back on its own after a
    reboot produces uncontrolled runs — so the app calls
    :meth:`reset_all_to_paused` once at startup.

    ``db_path`` accepts the JobQueries facade or a bare DSN string
    (BOUNDARY-DATA-001, #187); None keeps the in-memory legacy mode.
    """

    def __init__(
        self, db_path: ConnectSource | None = None, *, process_id: str | None = None
    ) -> None:
        # Facade (JobQueries) in production, bare DSN in tests, None = in-memory.
        # The source passes through untouched (#187): read_connection /
        # write_transaction / connect_database all accept ConnectSource, so
        # unwrapping a facade to its DSN here would be the very escape
        # BOUNDARY-DATA-001 retires.
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
                "select paused from worker_control_state where scope = %s",
                (f"workspace:{workspace_id}",),
            ).fetchone()
        return True if row is None else bool(row["paused"])

    def reset_all_to_paused(self) -> None:
        """Reset every scope to paused; called once at control-plane startup.

        Rows are deleted rather than set to paused=1: unknown workspaces
        default to paused anyway, and a fresh row keeps ``updated_by``
        attribution honest for the next explicit operator action."""
        if self._db_path is None:
            with self._lock:
                self._paused.clear()
            return
        with write_transaction(self._db_path) as conn:
            conn.execute("delete from worker_control_state")

    def _set(self, workspace_id: str, paused: bool) -> None:
        if self._db_path is None:
            with self._lock:
                self._paused[workspace_id] = paused
            return
        _persist_pause(self._db_path, f"workspace:{workspace_id}", paused, self._process_id)
