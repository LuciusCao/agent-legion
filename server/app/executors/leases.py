from __future__ import annotations

from datetime import datetime
from pathlib import Path

from server.app.db.connection import connect_sqlite
from server.app.db.schema import init_db
from server.app.executors._lease_claims import claim_lease
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

__all__ = ["ExecutorLeaseRepository", "_sqlite_timestamp"]


class ExecutorLeaseRepository:
    def __init__(self, path: Path):
        self.path = path
        init_db(path)

    def try_claim(self, request: LeaseClaimRequest) -> ClaimedExecution | None:
        conn = connect_sqlite(self.path)
        conn.isolation_level = None
        try:
            conn.execute("begin immediate")
            result = claim_lease(conn, request)
            if result is None:
                conn.execute("rollback")
            else:
                conn.execute("commit")
            return result
        except Exception:
            _rollback(conn)
            raise
        finally:
            conn.close()

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
        try:
            conn.execute("begin immediate")
            result_flag = finish_lease(conn, lease_id, result)
            conn.execute("commit")
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
        try:
            conn.execute("begin immediate")
            run_id = fail_without_lease(conn, request, error_message)
            conn.execute("commit")
            return run_id
        except Exception:
            _rollback(conn)
            raise
        finally:
            conn.close()

    def expire_stale(self, now: datetime) -> list[str]:
        conn = connect_sqlite(self.path)
        conn.isolation_level = None
        try:
            conn.execute("begin immediate")
            expired = expire_stale_leases(conn, now)
            conn.execute("commit")
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

    def _has_active(self, where: str, params: tuple[str, ...], now: datetime) -> bool:
        conn = connect_sqlite(self.path)
        try:
            row = conn.execute(
                f"select 1 from executor_leases where {where} "
                "and status='active' and expires_at>? limit 1",
                (*params, _sqlite_timestamp(now)),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def has_active_for_job(self, job_id: str, now: datetime) -> bool:
        return self._has_active("job_id=?", (job_id,), now)

    def has_active_for_node(self, job_id: str, node_key: str, now: datetime) -> bool:
        return self._has_active("job_id=? and node_key=?", (job_id, node_key), now)
