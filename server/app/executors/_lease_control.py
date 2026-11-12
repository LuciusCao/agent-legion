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


def job_mutation_lock_keys(conn: DatabaseConnection, job_ids: list[str]) -> dict[str, int]:
    """EXEC-GENERATION-001 全库唯一批序的排序键：每 job 的实际 32 位锁键
    hashtext('job-mutation:' || id)::int（#1346 / #1378 codex P2）。

    批序是纯 (锁键, job_id) 全序——不带 ws 分桶：分桶会让跨 workspace 批在
    碰撞别名对（W1 的 K1 与 W2 的 K1 是同一把锁）上仍按相反顺序取锁；池级锁
    由锁层级序（池锁先于 job-mutation）与 claim 批的 ws 预取纪律覆盖，不参与
    job 锁排序。advisory 锁键不依赖 jobs 行存在，直接按 id 计算。"""
    rows = conn.execute(
        "select t.id as id, hashtext('job-mutation:' || t.id)::int as job_lock_key"
        " from unnest(%s::text[]) as t(id)",
        (sorted({str(job_id) for job_id in job_ids}),),
    ).fetchall()
    return {str(r["id"]): int(r["job_lock_key"]) for r in rows}


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
