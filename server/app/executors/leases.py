from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from server.app.db.connection import connect_sqlite
from server.app.db.retry import retry_on_sqlite_lock
from server.app.db.schema import init_db
from server.app.events import JobEventManager
from server.app.executors import _lease_write_paths
from server.app.executors._lease_control import active_lease_counts
from server.app.executors._lease_lifecycle import fail_without_lease
from server.app.executors._lease_transactions import _rollback, _sqlite_timestamp
from server.app.executors.models import (
    ClaimedExecution,
    ConfigurationFailureRequest,
    ExecutionResult,
    LeaseClaimRequest,
)
from server.app.job_events import record_job_update
from server.app.jobs import JobQueries

logger = logging.getLogger(__name__)

__all__ = ["ExecutorLeaseRepository", "_sqlite_timestamp"]


class ExecutorLeaseRepository:
    def __init__(
        self,
        path: Path,
        job_db: JobQueries | None = None,
        job_event_manager: JobEventManager | None = None,
        data_dir: Path | None = None,
        job_event_buffer: Any | None = None,
    ):
        self.path = path
        self.job_db = job_db
        self.job_event_manager = job_event_manager
        self.data_dir = data_dir or path.parent
        self.job_event_buffer = job_event_buffer
        init_db(path)

    def _broadcast_job_update(self, job_id: str) -> None:
        try:
            if self.job_db is None or self.job_event_manager is None:
                return
            if self.job_event_buffer is not None:
                record_job_update(self.job_db, self.job_event_buffer, job_id)
                return
            job = self.job_db.get_job(job_id)
            workspace_id = str(job.get("workspace_id", "")) if job else ""
            if not workspace_id:
                return
            stats = self.job_db.count_jobs_by_status(workspace_id)
            self.job_event_manager.broadcast_job_updated(workspace_id, job_id, stats)
        except Exception:
            logger.exception("Failed to broadcast job update for %s", job_id)

    # Write paths delegate to _lease_write_paths so each retry attempt runs the
    # full connect-and-transact unit on a fresh connection.

    def try_claim(self, request: LeaseClaimRequest) -> ClaimedExecution | None:
        return retry_on_sqlite_lock(lambda: _lease_write_paths.try_claim(self, request))

    def heartbeat(self, lease_id: str, ttl_seconds: int) -> bool:
        return retry_on_sqlite_lock(
            lambda: _lease_write_paths.heartbeat(self, lease_id, ttl_seconds)
        )

    def finish(self, lease_id: str, result: ExecutionResult) -> bool:
        return retry_on_sqlite_lock(lambda: _lease_write_paths.finish(self, lease_id, result))

    def fail_without_lease(
        self, request: ConfigurationFailureRequest, error_message: str
    ) -> int | None:
        conn = connect_sqlite(self.path)
        conn.isolation_level = None
        job_id = request.job_id
        run_id: int | None = None
        try:
            conn.execute("begin immediate")
            run_id = fail_without_lease(conn, request, error_message, self.data_dir)
            conn.execute("commit")
            self._broadcast_job_update(job_id)
            return run_id
        except Exception:
            _rollback(conn)
            raise
        finally:
            conn.close()

    def expire_stale(self, now: datetime) -> list[str]:
        return retry_on_sqlite_lock(lambda: _lease_write_paths.expire_stale(self, now))

    def active_counts(self, executor_id: str) -> dict[str, int]:
        conn = connect_sqlite(self.path)
        try:
            return active_lease_counts(conn, executor_id)
        finally:
            conn.close()

    def has_active_for_job(self, job_id: str, now: datetime) -> bool:
        conn = connect_sqlite(self.path)
        try:
            row = conn.execute(
                "select 1 from executor_leases where job_id=? and status='active' and expires_at>? limit 1",
                (job_id, _sqlite_timestamp(now)),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def has_active_for_node(self, job_id: str, node_key: str, now: datetime) -> bool:
        conn = connect_sqlite(self.path)
        try:
            row = conn.execute(
                "select 1 from executor_leases where job_id=? and node_key=? and status='active' and expires_at>? limit 1",
                (job_id, node_key, _sqlite_timestamp(now)),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def recover_orphaned_running_jobs(self, now: datetime) -> list[str]:
        """Reset jobs stuck in 'running' with no active lease back to 'queued'."""
        return retry_on_sqlite_lock(
            lambda: _lease_write_paths.recover_orphaned_running_jobs(self, now)
        )
