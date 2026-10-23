from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from server.app.db.connection import DatabaseConnection
from server.app.executors._lease_transactions import database_timestamp
from server.app.executors.models import LeaseClaimRequest

# 与 mark_scan.TERMINAL_STATUSES 同源（executors 不反向依赖 workflow_worker）。
TERMINAL_JOB_STATUSES = ("completed", "failed")


def _read_job_execution_control(conn: DatabaseConnection, job_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        select execution_mode, target_node_key, execution_paused, pause_reason, status,
               execution_generation
        from jobs
        where id=%s
        """,
        (job_id,),
    ).fetchone()
    if row is None:
        return {
            "execution_mode": "full",
            "target_node_key": None,
            "execution_paused": False,
            "pause_reason": "",
            "status": None,
            "execution_generation": None,
        }
    return {
        "execution_mode": row["execution_mode"],
        "target_node_key": row["target_node_key"],
        "execution_paused": bool(row["execution_paused"]),
        "pause_reason": row["pause_reason"],
        "status": row["status"],
        "execution_generation": int(row["execution_generation"]),
    }


def _execution_control_rejects_claim(
    request: LeaseClaimRequest,
    current_control: dict[str, Any],
) -> bool:
    """Return True when the claim snapshot no longer authorizes the node."""
    if current_control["execution_paused"]:
        return True
    if current_control["execution_mode"] != request.execution_mode:
        return True
    if request.execution_mode == "full":
        return False
    if request.execution_mode == "until_node":
        if current_control["execution_mode"] != "until_node":
            return True
        if current_control["target_node_key"] != request.target_node_key:
            return True
        return request.node_key not in request.allowed_node_keys


def lock_job_mutation_and_read_generation(conn: DatabaseConnection, job_id: str) -> int | None:
    """Take the per-job mutation advisory lock and read the current epoch.

    EXEC-GENERATION-001 (#759 phase 1d): finish/fail/expire/recover writers
    serialize with the mutation side (``lease_guarded_mutation``) on this lock
    before their CAS check; taking it re-reads the jobs row against a state no
    concurrent reset can still change. None when the jobs row is gone (every
    expected epoch then counts as stale).
    """
    conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (f"job-mutation:{job_id}",))
    row = conn.execute("select execution_generation from jobs where id=%s", (job_id,)).fetchone()
    return int(row["execution_generation"]) if row is not None else None


# Sort sentinel for batch items whose workspace lock key cannot be resolved
# (the jobs row vanished before the batch's lookup — the item then fails the
# generation CAS without writing). hashtext's int domain is signed 32-bit, so
# -(2**31) is a real possible key value; the sentinel only needs a
# deterministic position, and the job_id tiebreak keeps even that collision
# consistent across batches.
_ORPHAN_WS_LOCK_KEY = -(2**31)


def batch_lock_keys_by_job(
    conn: DatabaseConnection, job_ids: list[str]
) -> dict[str, tuple[int, int]]:
    """EXEC-GENERATION-001 全库唯一批序的排序键：每 job 的
    (hashtext('agent-ws:' || workspace_id)::int, hashtext('job-mutation:' || id)::int)。

    ws 分量与 agent claim 批的 ``_lock_order_sorted``（claim_scan 的
    ws_lock_key / job_lock_key 列）同源同事。job 分量是 job-mutation 锁的
    **实际** 32 位锁键（#1346）：锁资源是 hashtext(key) 而非 id 文本，按 id
    文本序取锁会让碰撞四元组（a<b<c<d 且 hash(a)=hash(d)、hash(b)=hash(c)）
    把两个并发批的取锁序折叠成 AB-BA——批序必须建在真实锁键上，id 只作同键
    决胜。jobs 行已消失的 id 的 ws 分量取 ``_ORPHAN_WS_LOCK_KEY``（advisory
    锁键不依赖行存在，job 分量仍按真实键算）。"""
    rows = conn.execute(
        "select t.id as id, coalesce(hashtext('agent-ws:' || j.workspace_id)::int, %s) as ws_lock_key, hashtext('job-mutation:' || t.id)::int as job_lock_key from unnest(%s::text[]) as t(id) left join jobs j on j.id = t.id",
        (_ORPHAN_WS_LOCK_KEY, sorted({str(job_id) for job_id in job_ids})),
    ).fetchall()
    return {str(r["id"]): (int(r["ws_lock_key"]), int(r["job_lock_key"])) for r in rows}


def sync_job_status(conn: DatabaseConnection, job_id: str) -> None:
    still_running = conn.execute(
        "select 1 from job_nodes where job_id=%s and status='running'",
        (job_id,),
    ).fetchone()
    if still_running is not None:
        conn.execute(
            "update jobs set status=%s, updated_at=%s where id=%s",
            ("running", database_timestamp(datetime.now(UTC)), job_id),
        )
        return

    any_failed = conn.execute(
        "select 1 from job_nodes where job_id=%s and status='failed'",
        (job_id,),
    ).fetchone()
    if any_failed is not None:
        conn.execute(
            "update jobs set status=%s, updated_at=%s where id=%s",
            ("failed", database_timestamp(datetime.now(UTC)), job_id),
        )
        return

    paused = conn.execute(
        "select 1 from jobs where id=%s and execution_paused=1",
        (job_id,),
    ).fetchone()
    if paused is not None:
        conn.execute(
            "update jobs set status=%s, updated_at=%s where id=%s",
            ("paused", database_timestamp(datetime.now(UTC)), job_id),
        )
        return

    # A parked approval gate surfaces as the job status once nothing is
    # running: "a human is needed" outranks the generic queued. Parallel
    # branches that later claim flip the job back to running above.
    awaiting = conn.execute(
        "select 1 from job_nodes where job_id=%s and status='awaiting_approval'",
        (job_id,),
    ).fetchone()
    if awaiting is not None:
        conn.execute(
            "update jobs set status=%s, updated_at=%s where id=%s",
            ("awaiting_approval", database_timestamp(datetime.now(UTC)), job_id),
        )
        return

    non_terminal = conn.execute(
        """
        select 1 from job_nodes
        where job_id=%s and status not in ('completed', 'not_applicable')
        """,
        (job_id,),
    ).fetchone()
    if non_terminal is not None:
        conn.execute(
            "update jobs set status=%s, updated_at=%s where id=%s",
            ("queued", database_timestamp(datetime.now(UTC)), job_id),
        )
        return

    conn.execute(
        "update jobs set status=%s, updated_at=%s where id=%s",
        ("completed", database_timestamp(datetime.now(UTC)), job_id),
    )


def _pause_job_on_target_completion(
    conn: DatabaseConnection,
    job_id: str,
    completed_node_key: str,
    now_str: str,
) -> None:
    job = conn.execute(
        """
        select execution_mode, target_node_key, status
        from jobs
        where id=%s
        """,
        (job_id,),
    ).fetchone()
    if (
        job is not None
        and job["execution_mode"] == "until_node"
        and job["target_node_key"] == completed_node_key
        and job["status"] != "completed"
    ):
        conn.execute(
            """
            update jobs
            set status='paused',
                execution_paused=1,
                pause_reason='target_reached',
                updated_at=%s
            where id=%s
            """,
            (now_str, job_id),
        )


def active_lease_counts(conn: DatabaseConnection, executor_id: str) -> dict[str, int]:
    """Active lease counts for the pool: ``global`` plus one key per workspace."""
    now_str = database_timestamp(datetime.now(UTC))
    counts: dict[str, int] = {"global": 0}

    global_row = conn.execute(
        """
        select count(*) as cnt
        from executor_leases
        where executor_id=%s and status='active' and expires_at>%s
        """,
        (executor_id, now_str),
    ).fetchone()
    counts["global"] = int(global_row["cnt"]) if global_row is not None else 0

    rows = conn.execute(
        """
        select workspace_id, count(*) as cnt
        from executor_leases
        where executor_id=%s and status='active' and expires_at>%s
        group by workspace_id
        """,
        (executor_id, now_str),
    ).fetchall()
    for row in rows:
        counts[row["workspace_id"]] = row["cnt"]
    return counts
