"""Executor lease repository — the capacity gate AGENTS.md §6 names (#187)."""

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
from server.app.executors._lease_approval import park_awaiting_approval_repo
from server.app.executors._lease_config_failure import fail_without_lease
from server.app.executors._lease_control import active_lease_counts
from server.app.executors._lease_shard_fail import fail_shard_repo
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
        # must not. Step 3: the facade's `.path` is private, so the DSN comes
        # from `dsn_identity` — the facade's only public accessor.
        if isinstance(job_db, str):
            self.job_db = None
            self.path: str = job_db
        else:
            self.job_db = job_db
            self.path = job_db.dsn_identity
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
            # #204 broad-except audit: fire-and-forget observability, called
            # only AFTER the lease/write transaction already committed (every
            # call site sits below the `with write_transaction` block). A SSE
            # refresh failure must never roll back or fail a claim/finish that
            # already succeeded — the stats are trigger-maintained
            # (DB-JOB-STATUS-COUNTS-001), so the very next state change
            # re-broadcasts the correct numbers and the missed one self-heals.
            # The outcome space here is the DB read surface plus the bus, not
            # a business family; logger.exception keeps the traceback.
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

    def fail_shard(
        self,
        job_id: str,
        node_key: str,
        shard_index: int,
        error_message: str,
        *,
        dispatch_generation: str = "",
        log_path: str = "",
    ) -> bool:
        """Shard-granularity sibling of ``fail_without_lease`` (#520 review).

        PR #520 review P2-2：shard 级失败此前由调用方直接开事务提交，绕过
        了这里 commit 后的 ``_broadcast_job_update``——SSE 客户端会一直显
        示旧状态直到手动刷新。写路径收进仓库方法后，广播/「提交成功后才
        广播」的纪律与 ``fail_without_lease`` 完全同源（含冲突重试，事务
        体在 ``_lease_shard_fail.fail_shard_repo``）。返回 False = 身份校验
        失败（rerun 重建后的新轮 shard，迟到的旧轮失败被丢弃，见
        ``_lease_shard_fail`` 的 P1 注释）。
        """
        terminated = fail_shard_repo(
            self,
            job_id,
            node_key,
            shard_index,
            error_message,
            dispatch_generation=dispatch_generation,
            log_path=log_path,
        )
        if terminated:
            # Broadcast only after the commit has succeeded, never inside the tx.
            self._broadcast_job_update(job_id)
        return terminated

    def park_awaiting_approval(self, job_id: str, node_key: str) -> bool:
        """Park a ready approval node (EXEC-APPROVAL-001); no lease, no node_run."""
        if retry_on_database_conflict(lambda: park_awaiting_approval_repo(self, job_id, node_key)):
            self._broadcast_job_update(job_id)
            return True
        return False

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
        """Bulk ``has_active_for_node`` for read-only batch checks (rerun preview)."""
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
