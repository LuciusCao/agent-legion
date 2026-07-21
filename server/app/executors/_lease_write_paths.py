"""Write-path transaction bodies for ExecutorLeaseRepository.

Each function owns its connect-and-transact unit via
``server.app.db.transaction.write_transaction`` (commit on success, rollback
on error), so the repository can retry the whole unit on transient PostgreSQL
transaction conflicts and every attempt starts from a fresh connection.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from server.app.db.transaction import read_connection, write_transaction
from server.app.executors._lease_claims import claim_lease
from server.app.executors._lease_control import _sync_job_status
from server.app.executors._lease_lifecycle import (
    expire_stale_leases,
    finish_lease,
    heartbeat_lease,
)
from server.app.executors._lease_transactions import _database_timestamp
from server.app.executors.models import (
    ClaimedExecution,
    ExecutionResult,
    LeaseClaimRequest,
)
from server.app.services.pi_event_compression import compress_pi_events
from server.app.services.token_usage_lease import capture_token_usage_after_lease_finish
from server.app.storage_paths import resolve_data_path

if TYPE_CHECKING:
    from server.app.executors.leases import ExecutorLeaseRepository


def try_claim(repo: ExecutorLeaseRepository, request: LeaseClaimRequest) -> ClaimedExecution | None:
    with write_transaction(repo.path) as conn:
        result = claim_lease(conn, request, repo.data_dir)
    # claim_lease returns None without modifying any rows, so letting the
    # block above commit an empty transaction is equivalent to the old
    # explicit rollback. Broadcast only after the commit has succeeded,
    # never inside the transaction.
    if result is not None:
        repo._broadcast_job_update(str(result.job_id))
    return result


def heartbeat(repo: ExecutorLeaseRepository, lease_id: str, ttl_seconds: int) -> bool:
    with write_transaction(repo.path) as conn:
        return heartbeat_lease(conn, lease_id, ttl_seconds)


def finish(repo: ExecutorLeaseRepository, lease_id: str, result: ExecutionResult) -> bool:
    with write_transaction(repo.path) as conn:
        lease = conn.execute(
            "select job_id from executor_leases where id=?", (lease_id,)
        ).fetchone()
        job_id = str(lease["job_id"]) if lease else None
        result_flag = finish_lease(conn, lease_id, result, repo.data_dir)

    # Parse events.jsonl outside the main write transaction; the
    # capture helper opens its own short write tx only for the persist.
    # The helper still expects a caller-provided connection (its own
    # migration is Task 3), so hand it a fresh one now that the commit
    # has landed.
    if result_flag and result.status in ("completed", "failed") and repo.data_dir is not None:
        with read_connection(repo.path) as read_conn:
            capture_token_usage_after_lease_finish(read_conn, lease_id, repo.data_dir)
            if result.run_dir:
                run_dir = resolve_data_path(result.run_dir, repo.data_dir, allow_missing=True)
                compress_pi_events(run_dir / "events.jsonl")

    # Broadcast only after the commit has succeeded, never inside the tx.
    if job_id is not None and result_flag:
        repo._broadcast_job_update(job_id)
    return result_flag


def expire_stale(repo: ExecutorLeaseRepository, now: datetime) -> list[str]:
    with write_transaction(repo.path) as conn:
        rows = conn.execute(
            "select job_id from executor_leases where status='active' and expires_at<=?",
            (_database_timestamp(now),),
        ).fetchall()
        affected_job_ids = list({str(row["job_id"]) for row in rows})
        expired = expire_stale_leases(conn, now)
    # Broadcast only after the commit has succeeded, never inside the tx.
    for job_id in affected_job_ids:
        repo._broadcast_job_update(job_id)
    return expired


def recover_orphaned_running_jobs(repo: ExecutorLeaseRepository, now: datetime) -> list[str]:
    """Reset jobs stuck in 'running' with no active lease back to 'queued'."""
    now_str = _database_timestamp(now)
    with write_transaction(repo.path) as conn:
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
        if recovered:
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
    # Broadcast only after the commit has succeeded, never inside the tx.
    for job_id in recovered:
        repo._broadcast_job_update(job_id)
    return recovered
