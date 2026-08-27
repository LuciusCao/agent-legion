from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from server.app.db.retry import retry_on_database_conflict
from server.app.db.schema import init_db
from server.app.db.transaction import read_connection, write_transaction
from server.app.events import JobEventManager
from server.app.events.aggregator import record_job_update
from server.app.executors import _lease_write_paths
from server.app.executors._lease_config_failure import fail_without_lease
from server.app.executors._lease_control import active_lease_counts
from server.app.executors._lease_transactions import database_timestamp
from server.app.executors.models import (
    ClaimedExecution,
    ConfigurationFailureRequest,
    ExecutionResult,
    LeaseClaimRequest,
)
from server.app.jobs import JobQueries

logger = logging.getLogger(__name__)

__all__ = ["ExecutorLeaseRepository"]


class ExecutorLeaseRepository:
    def __init__(
        self,
        job_db: JobQueries | str,
        job_event_manager: JobEventManager | None = None,
        *,
        data_dir: Path,
        job_event_buffer: Any | None = None,
    ):
        # #187: the repository is constructed from the JobQueries facade (a
        # bare DSN string stays accepted so tests and the transition period
        # keep working). It lives BELOW the service boundary on purpose —
        # like queries/atomic_mutations, it is one of the data-layer-adjacent
        # components that legitimately hold the connection source; services
        # must not.
        if isinstance(job_db, str):
            self.job_db = None
            self.path: str = job_db
        else:
            self.job_db = job_db
            self.path = job_db.path
        self.job_event_manager = job_event_manager
        self.data_dir = data_dir
        self.job_event_buffer = job_event_buffer
        init_db(self.path)

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

    # Write paths delegate to _lease_write_paths (one connect-and-transact
    # unit per call, retried on database conflicts).

    def try_claim(self, request: LeaseClaimRequest) -> ClaimedExecution | None:
        return retry_on_database_conflict(lambda: _lease_write_paths.try_claim(self, request))

    def try_claim_many(self, requests: list[LeaseClaimRequest]) -> list[ClaimedExecution | None]:
        return retry_on_database_conflict(lambda: _lease_write_paths.try_claim_many(self, requests))

    def heartbeat(self, lease_id: str, ttl_seconds: int) -> bool:
        return retry_on_database_conflict(
            lambda: _lease_write_paths.heartbeat(self, lease_id, ttl_seconds)
        )

    def finish(self, lease_id: str, result: ExecutionResult) -> bool:
        return retry_on_database_conflict(lambda: _lease_write_paths.finish(self, lease_id, result))

    def fail_without_lease(
        self, request: ConfigurationFailureRequest, error_message: str
    ) -> int | None:
        job_id = request.job_id
        with write_transaction(self.path) as conn:
            run_id = fail_without_lease(conn, request, error_message, self.data_dir)
        # Broadcast only after the commit has succeeded, never inside the tx.
        self._broadcast_job_update(job_id)
        return run_id

    def expire_stale(self, now: datetime) -> list[str]:
        return retry_on_database_conflict(lambda: _lease_write_paths.expire_stale(self, now))

    def active_counts(self, executor_id: str) -> dict[str, int]:
        with read_connection(self.path) as conn:
            return active_lease_counts(conn, executor_id)

    def has_active_for_job(self, job_id: str, now: datetime) -> bool:
        with read_connection(self.path) as conn:
            row = conn.execute(
                "select 1 from executor_leases where job_id=%s and status='active' and expires_at>%s limit 1",
                (job_id, database_timestamp(now)),
            ).fetchone()
            return row is not None

    def has_active_for_node(self, job_id: str, node_key: str, now: datetime) -> bool:
        with read_connection(self.path) as conn:
            row = conn.execute(
                "select 1 from executor_leases where job_id=%s and node_key=%s and status='active' and expires_at>%s limit 1",
                (job_id, node_key, database_timestamp(now)),
            ).fetchone()
            return row is not None

    def active_lease_node_keys_for_jobs(
        self, job_ids: Sequence[str], now: datetime
    ) -> set[tuple[str, str]]:
        """Bulk form of ``has_active_for_node``: (job_id, node_key) pairs with
        an active lease, for read-only batch checks (rerun preview)."""
        ids = [str(job_id) for job_id in job_ids]
        if not ids:
            return set()
        placeholders = ",".join("%s" for _ in ids)
        with read_connection(self.path) as conn:
            rows = conn.execute(
                f"select job_id, node_key from executor_leases"
                f" where job_id in ({placeholders}) and status='active' and expires_at>%s",
                (*ids, database_timestamp(now)),
            ).fetchall()
        return {(str(row["job_id"]), str(row["node_key"])) for row in rows}

    def recover_orphaned_running_jobs(self, now: datetime) -> list[str]:
        """Reset jobs stuck in 'running' with no active lease back to 'queued'."""
        return retry_on_database_conflict(
            lambda: _lease_write_paths.recover_orphaned_running_jobs(self, now)
        )
