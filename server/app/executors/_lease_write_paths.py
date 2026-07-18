"""Write-path transaction bodies for ExecutorLeaseRepository.

Each function owns its connect-and-transact unit and rolls back on error, so
the repository can retry the whole unit on transient SQLite lock errors and
every attempt starts from a fresh connection.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from server.app.db.connection import connect_sqlite
from server.app.executors._lease_claims import claim_lease
from server.app.executors._lease_control import _sync_job_status
from server.app.executors._lease_lifecycle import (
    expire_stale_leases,
    finish_lease,
    heartbeat_lease,
)
from server.app.executors._lease_transactions import _rollback, _sqlite_timestamp
from server.app.executors.models import (
    ClaimedExecution,
    ExecutionResult,
    LeaseClaimRequest,
)
from server.app.services.token_usage_lease import capture_token_usage_after_lease_finish

if TYPE_CHECKING:
    from server.app.executors.leases import ExecutorLeaseRepository


def try_claim(repo: ExecutorLeaseRepository, request: LeaseClaimRequest) -> ClaimedExecution | None:
    conn = connect_sqlite(repo.path)
    conn.isolation_level = None
    claimed: ClaimedExecution | None = None
    try:
        conn.execute("begin immediate")
        result = claim_lease(conn, request, repo.data_dir)
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
            repo._broadcast_job_update(str(claimed.job_id))


def heartbeat(repo: ExecutorLeaseRepository, lease_id: str, ttl_seconds: int) -> bool:
    conn = connect_sqlite(repo.path)
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


def finish(repo: ExecutorLeaseRepository, lease_id: str, result: ExecutionResult) -> bool:
    conn = connect_sqlite(repo.path)
    conn.isolation_level = None
    job_id: str | None = None
    result_flag = False
    try:
        conn.execute("begin immediate")
        lease = conn.execute(
            "select job_id from executor_leases where id=?", (lease_id,)
        ).fetchone()
        job_id = str(lease["job_id"]) if lease else None
        result_flag = finish_lease(conn, lease_id, result, repo.data_dir)
        conn.execute("commit")

        # Parse events.jsonl outside the main write transaction; the
        # capture helper opens its own short write tx only for the persist.
        if result_flag and result.status in ("completed", "failed") and repo.data_dir is not None:
            capture_token_usage_after_lease_finish(conn, lease_id, repo.data_dir)

        if job_id is not None and result_flag:
            repo._broadcast_job_update(job_id)
        return result_flag
    except Exception:
        _rollback(conn)
        raise
    finally:
        conn.close()


def expire_stale(repo: ExecutorLeaseRepository, now: datetime) -> list[str]:
    conn = connect_sqlite(repo.path)
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
            repo._broadcast_job_update(job_id)
        return expired
    except Exception:
        _rollback(conn)
        raise
    finally:
        conn.close()


def recover_orphaned_running_jobs(repo: ExecutorLeaseRepository, now: datetime) -> list[str]:
    """Reset jobs stuck in 'running' with no active lease back to 'queued'."""
    conn = connect_sqlite(repo.path)
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
                finished_at=null,
                created_at=current_timestamp
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
            repo._broadcast_job_update(job_id)
        return recovered
    except Exception:
        _rollback(conn)
        raise
    finally:
        conn.close()
