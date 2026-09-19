"""Write-path transaction bodies for ExecutorLeaseRepository.

Each function owns its connect-and-transact unit via ``write_transaction``
(commit on success, rollback on error), so the repository can retry the whole
unit on transient PostgreSQL transaction conflicts.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from server.app.db.connection import DatabaseConnection
from server.app.db.transaction import write_transaction
from server.app.executors._lease_claims import claim_lease
from server.app.executors._lease_control import (
    _ORPHAN_WS_LOCK_KEY,
    lock_job_mutation_and_read_generation,
    sync_job_status,
    ws_lock_keys_by_job,
)
from server.app.executors._lease_lifecycle import (
    expire_stale_leases,
    finish_lease,
    heartbeat_lease,
)
from server.app.executors._lease_transactions import database_timestamp
from server.app.executors.models import (
    ClaimedExecution,
    ExecutionResult,
    LeaseClaimRequest,
)
from server.app.workflows.sharding import delete_shards

if TYPE_CHECKING:
    from server.app.executors.leases import ExecutorLeaseRepository


def try_claim(repo: ExecutorLeaseRepository, request: LeaseClaimRequest) -> ClaimedExecution | None:
    with write_transaction(repo.path) as conn:
        result = claim_lease(conn, request, repo.data_dir)
    # claim_lease returns None without modifying any rows; committing the empty
    # transaction is equivalent to the old rollback. Broadcast only post-commit.
    if result is not None:
        _broadcast_committed(repo, [str(result.job_id)])
    return result


def try_claim_many(
    repo: ExecutorLeaseRepository, requests: list[LeaseClaimRequest]
) -> list[ClaimedExecution | None]:
    """Claim a batch of nodes in one transaction; None entries on capacity loss.

    Claims run in the single global job-mutation batch order shared with
    ``finish_many`` / expire / recover / the agent sweep and the agent claim
    batch's agent block (EXEC-GENERATION-001, #759 phases 1c+7):
    ``(hashtext('agent-ws:' || workspace_id)::int, job_id)`` — every claim
    takes the ``job-mutation:<job_id>`` advisory xact lock, never released
    before COMMIT, so one cross-batch order prevents AB-BA on that domain.
    The agent batch's code-first block stays job_id-ordered (code candidates
    take no ``agent-ws:*`` lock); a remote code candidate crossing this local
    batch on the same two jobs is the accepted residual, covered by the
    40P01 retry wrappers on both sides. Verdicts are reassembled in caller
    order.
    """
    with write_transaction(repo.path) as conn:
        # The ws lock key is server-side (hashtext), so resolve it for the
        # batch's jobs in one query before sorting.
        ws_keys = ws_lock_keys_by_job(conn, [request.job_id for request in requests])
        results: list[ClaimedExecution | None] = [None] * len(requests)
        for index, request in sorted(
            enumerate(requests),
            key=lambda e: (ws_keys.get(e[1].job_id, _ORPHAN_WS_LOCK_KEY), e[1].job_id, e[0]),
        ):
            results[index] = claim_lease(conn, request, repo.data_dir)
    _broadcast_committed(repo, [str(r.job_id) for r in results if r is not None])
    return results


def _broadcast_committed(repo: ExecutorLeaseRepository, job_ids: list[str]) -> None:
    """Broadcast job updates only after the commit landed (deduped)."""
    for job_id in set(job_ids):
        repo._broadcast_job_update(job_id)


def heartbeat(repo: ExecutorLeaseRepository, lease_id: str, ttl_seconds: int) -> bool:
    with write_transaction(repo.path) as conn:
        return heartbeat_lease(conn, lease_id, ttl_seconds)


def finish(
    repo: ExecutorLeaseRepository,
    lease_id: str,
    result: ExecutionResult,
    *,
    stage_timer: Any | None = None,
) -> bool:
    with write_transaction(repo.path) as conn:
        lease = conn.execute(
            "select job_id from executor_leases where id=%s", (lease_id,)
        ).fetchone()
        job_id = str(lease["job_id"]) if lease else None
        result_flag = finish_lease(conn, lease_id, result, repo.data_dir)
    # #521 result-stage split: the terminal-state write transaction is its
    # own segment; the events post-processing below (two full events.jsonl
    # scans today) is the next one — marked only when that work actually
    # ran (subagent review on #530: a 409-lease-inactive or cancelled
    # commit must not report a misleading events=0.0ms segment).
    # ``stage_timer`` is None on every code-plane caller — only the Agent
    # result commit threads it through.
    _mark_result_stage(stage_timer, "lease_write")

    # Parse events.jsonl outside the main write transaction; the
    # capture helper opens its own short write tx only for the persist.
    # The helper still expects a caller-provided connection (its own
    # migration is Task 3), so hand it a fresh one now that the commit
    # has landed.
    events_ran = result_flag and result.status in ("completed", "failed")
    if events_ran:
        finish_events_post_processing(repo, lease_id, result)
        _mark_result_stage(stage_timer, "events")

    # Broadcast only after the commit has succeeded, never inside the tx.
    if job_id is not None and result_flag:
        _broadcast_committed(repo, [job_id])
    return result_flag


def finish_events_post_processing(
    repo: ExecutorLeaseRepository, lease_id: str, result: ExecutionResult
) -> None:
    """Events.jsonl token capture + PI compression for one finished lease.

    Completed/failed only (both finish paths' family gate — the direct
    path and the batched arm's post-commit callback); the data_dir guard
    keeps the exact defensive shape. Parse events outside the main write
    transaction; the capture helper opens its own short tx for the persist.
    """
    from server.app.db.transaction import read_connection
    from server.app.services.token_usage_lease import capture_token_usage_after_lease_finish
    from server.app.storage_paths import resolve_data_path
    from shared.pi_events import compress_pi_events

    if repo.data_dir is not None:
        with read_connection(repo.path) as read_conn:
            capture_token_usage_after_lease_finish(read_conn, lease_id, repo.data_dir)
        if result.run_dir:
            run_dir = resolve_data_path(result.run_dir, repo.data_dir, allow_missing=True)
            compress_pi_events(run_dir / "events.jsonl")


def _mark_result_stage(stage_timer: Any | None, name: str) -> None:
    """Close one #521 result-stage segment on an optional timer."""
    if stage_timer is not None:
        stage_timer.stage(name)


def expire_stale(repo: ExecutorLeaseRepository, now: datetime) -> list[str]:
    with write_transaction(repo.path) as conn:
        rows = conn.execute(
            "select job_id from executor_leases where status='active' and expires_at<=%s",
            (database_timestamp(now),),
        ).fetchall()
        affected_job_ids = [str(row["job_id"]) for row in rows]
        expired = expire_stale_leases(conn, now)
    _broadcast_committed(repo, affected_job_ids)
    return expired


def recover_orphaned_running_jobs(repo: ExecutorLeaseRepository, now: datetime) -> list[str]:
    """Reset jobs stuck in 'running' with no active lease back to 'queued'."""
    now_str = database_timestamp(now)
    with write_transaction(repo.path) as conn:
        rows = conn.execute(
            "select j.id, hashtext('agent-ws:' || j.workspace_id)::int as ws_lock_key from jobs j"
            " where j.status='running' and not exists"
            " (select 1 from executor_leases l where l.job_id=j.id and l.status='active')"
        ).fetchall()
        # EXEC-GENERATION-001：每个恢复都会取 job-mutation advisory xact 锁
        # （不随语句提前释放），全库批路径共用唯一序 (ws 锁键, job_id)——与
        # agent claim 批的 agent 块（claim_batch_tx._lock_order_sorted）及
        # finish_many/expire/sweep 相同，防批间 AB-BA。
        recovered = [
            job_id
            for job_id in (
                str(row["id"])
                for row in sorted(rows, key=lambda r: (int(r["ws_lock_key"]), str(r["id"])))
            )
            if _recover_orphaned_job(conn, job_id, now_str)
        ]
    _broadcast_committed(repo, recovered)
    return recovered


def _recover_orphaned_job(conn: DatabaseConnection, job_id: str, now_str: str) -> bool:
    """Reset one orphaned job's running nodes; False when a lease appeared.

    The ``not exists`` predicate rides on the UPDATE itself so PostgreSQL
    re-evaluates it against the latest committed leases: a node claimed
    concurrently after the candidate SELECT is never reset to 'pending'
    (which would double-execute it while its lease stays active).

    EXEC-GENERATION-001: under the job-mutation lock the reset is CAS-gated
    on the node row's epoch stamp — a running row stamped by an older epoch
    belongs to state a reset already superseded, so only the lease/run side
    may settle, never the job_nodes flip.
    """
    current_generation = lock_job_mutation_and_read_generation(conn, job_id)
    if current_generation is None:
        return False
    reset = conn.execute(
        """
        update job_nodes
        set status='pending',
            stale_reason='',
            error_message='',
            started_at=null,
            finished_at=null,
            created_at=current_timestamp
        where job_id=%s and status='running'
          and execution_generation=%s
          and not exists (
              select 1 from executor_leases l
              where l.job_id = job_nodes.job_id and l.status='active'
          )
        returning node_key
        """,
        (job_id, current_generation),
    ).fetchall()
    if not reset:
        # A concurrently claimed lease blocks the reset; the sweeper retries
        # next tick. A job with no running nodes at all only needs its status
        # resynced (stale 'running' with no lease).
        lease = conn.execute(
            "select 1 from executor_leases where job_id=%s and status='active' limit 1",
            (job_id,),
        ).fetchone()
        if lease is not None:
            return False
        sync_job_status(conn, job_id)
        return True
    # The reset above holds row locks on this job's running nodes until
    # commit, so no claim for those nodes can interleave with these follow-up
    # writes. A claim for a *different* (non-running) node of the same job can
    # still land in between, so the node_runs update carries the same guard.
    delete_shards(conn, job_id, [str(row["node_key"]) for row in reset])
    conn.execute(
        """
        update node_runs
        set status='failed',
            error_message='orphaned recovery',
            failure_category='technical',
            failure_detail='worker_orphaned',
            finished_at=%s
        where job_id=%s and status='running'
          and not exists (
              select 1 from executor_leases l
              where l.job_id = node_runs.job_id and l.status='active'
          )
        """,
        (now_str, job_id),
    )
    sync_job_status(conn, job_id)
    return True
