from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from server.app.db.connection import connect_sqlite
from server.app.db.schema import init_db
from server.app.events import JobEventManager
from server.app.executors._lease_claims import claim_lease
from server.app.executors._lease_control import _sync_job_status
from server.app.executors._lease_lifecycle import (
    active_lease_counts,
    expire_stale_leases,
    fail_without_lease,
    finish_lease,
    heartbeat_lease,
)
from server.app.executors._lease_transactions import _rollback, _sqlite_timestamp
from server.app.executors.models import (
    ClaimedExecution,
    ConfigurationFailureRequest,
    ExecutionResult,
    LeaseClaimRequest,
)
from server.app.jobs import JobQueries

logger = logging.getLogger(__name__)

__all__ = ["ExecutorLeaseRepository", "_sqlite_timestamp"]


class ExecutorLeaseRepository:
    def __init__(
        self,
        path: Path,
        job_db: JobQueries | None = None,
        job_event_manager: JobEventManager | None = None,
    ):
        self.path = path
        self.job_db = job_db
        self.job_event_manager = job_event_manager
        init_db(path)

    def _broadcast_job_update(self, job_id: str) -> None:
        try:
            if self.job_event_manager is None or self.job_db is None:
                return
            job = self.job_db.get_job(job_id)
            if job is None:
                return
            workspace_id = str(job.get("workspace_id", ""))
            if not workspace_id:
                return
            stats = self.job_db.count_jobs_by_status(workspace_id)
            self.job_event_manager.broadcast_job_updated(workspace_id, job_id, stats)
        except Exception:
            logger.exception("Failed to broadcast job update for %s", job_id)

    def try_claim(self, request: LeaseClaimRequest) -> ClaimedExecution | None:
        conn = connect_sqlite(self.path)
        conn.isolation_level = None
        claimed: ClaimedExecution | None = None
        try:
            conn.execute("begin immediate")
            result = claim_lease(conn, request)
            if result is None:
                conn.execute("rollback")
            else:
                conn.execute("commit")
                claimed = result
            return result
        except Exception:
            _rollback(conn)
            raise
        finally:
            conn.close()
            if claimed is not None:
                self._broadcast_job_update(str(claimed.job_id))

    def heartbeat(self, lease_id: str, ttl_seconds: int) -> bool:
        conn = connect_sqlite(self.path)
        conn.isolation_level = None
        try:
            conn.execute("begin immediate")
            result = heartbeat_lease(conn, lease_id, ttl_seconds)
            conn.execute("commit")
            return result
        except Exception:
            _rollback(conn)
            raise
        finally:
            conn.close()

    def finish(self, lease_id: str, result: ExecutionResult) -> bool:
        conn = connect_sqlite(self.path)
        conn.isolation_level = None
        job_id: str | None = None
        result_flag = False
        try:
            conn.execute("begin immediate")
            lease = conn.execute(
                "select job_id from executor_leases where id=?", (lease_id,)
            ).fetchone()
            job_id = str(lease["job_id"]) if lease else None
            result_flag = finish_lease(conn, lease_id, result)
            conn.execute("commit")
            if job_id is not None and result_flag:
                self._broadcast_job_update(job_id)
            return result_flag
        except Exception:
            _rollback(conn)
            raise
        finally:
            conn.close()

    def fail_without_lease(
        self, request: ConfigurationFailureRequest, error_message: str
    ) -> int | None:
        conn = connect_sqlite(self.path)
        conn.isolation_level = None
        job_id = request.job_id
        run_id: int | None = None
        try:
            conn.execute("begin immediate")
            run_id = fail_without_lease(conn, request, error_message)
            conn.execute("commit")
            self._broadcast_job_update(job_id)
            return run_id
        except Exception:
            _rollback(conn)
            raise
        finally:
            conn.close()

    def expire_stale(self, now: datetime) -> list[str]:
        conn = connect_sqlite(self.path)
        conn.isolation_level = None
        affected_job_ids: list[str] = []
        try:
            conn.execute("begin immediate")
            rows = conn.execute(
                "select job_id from executor_leases where status='active' and expires_at<=?",
                (_sqlite_timestamp(now),),
            ).fetchall()
            affected_job_ids = list({str(row["job_id"]) for row in rows})
            expired = expire_stale_leases(conn, now)
            conn.execute("commit")
            for job_id in affected_job_ids:
                self._broadcast_job_update(job_id)
            return expired
        except Exception:
            _rollback(conn)
            raise
        finally:
            conn.close()

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
        """Return jobs stuck in 'running' with no active lease back to 'queued'.

        A job is orphaned when jobs.status='running' but no executor_leases
        row exists with the same job_id and status='active'. Any job_nodes
        still marked 'running' for that job are reset to 'pending' so the
        scheduler can re-evaluate which node to run next.
        """
        conn = connect_sqlite(self.path)
        conn.isolation_level = None
        recovered: list[str] = []
        now_str = _sqlite_timestamp(now)
        try:
            conn.execute("begin immediate")
            rows = conn.execute(
                """
                select j.id
                from jobs j
                where j.status='running'
                  and not exists (
                      select 1 from executor_leases l
                      where l.job_id = j.id and l.status='active'
                  )
                """
            ).fetchall()
            recovered = [str(row["id"]) for row in rows]
            if not recovered:
                conn.execute("commit")
                return recovered

            placeholders = ",".join("?" * len(recovered))
            conn.execute(
                f"""
                update job_nodes
                set status='pending',
                    stale_reason='',
                    error_message='',
                    started_at=null,
                    finished_at=null
                where job_id in ({placeholders}) and status='running'
                """,
                recovered,
            )
            conn.execute(
                f"""
                update node_runs
                set status='failed',
                    error_message='orphaned recovery',
                    finished_at=?
                where job_id in ({placeholders}) and status='running'
                """,
                (now_str, *recovered),
            )
            for job_id in recovered:
                _sync_job_status(conn, job_id)
            conn.execute("commit")
            for job_id in recovered:
                self._broadcast_job_update(job_id)
            return recovered
        except Exception:
            _rollback(conn)
            raise
        finally:
            conn.close()
