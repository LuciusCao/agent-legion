"""非重置类的原子 job mutation（#759 预算拆分自 ``atomic_mutations``）。

EXEC-GENERATION-001：``resume_job``（paused→queued 只是恢复调度）、
``delete_job``（删除无所谓代次）与 ``prepare_replay_copy``（replay 副本
初始化，非既有 job 的执行状态重置）都不 bump ``jobs.execution_generation``；
重置类 mutation（rerun / run-to / upgrade）见 ``atomic_mutations`` 与
``workflow_upgrade_mutation``。
"""

from __future__ import annotations

from collections.abc import Sequence

from server.app.db.connection import DatabaseConnection


class JobMutationConflict(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def prepare_replay_copy(
    conn: DatabaseConnection,
    job_id: str,
    *,
    completed_nodes: Sequence[str],
    skipped_nodes: Sequence[str],
) -> None:
    """Set up a quality-replay copy job's node states (schema v29).

    Upstream nodes are marked completed without running (their frozen output
    files were copied into the copy's job directory); downstream nodes are
    marked not_applicable so the copy never schedules past the replayed node
    and converges to completed once the target finishes.
    """
    for node_key in completed_nodes:
        cursor = conn.execute(
            """
            update job_nodes
            set status='completed', finished_at=current_timestamp
            where job_id=%s and node_key=%s and status='pending'
            """,
            (job_id, node_key),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"Unknown job node: {job_id}.{node_key}")
    if skipped_nodes:
        placeholders = ",".join("%s" for _ in skipped_nodes)
        conn.execute(
            f"""
            update job_nodes
            set status='not_applicable', stale_reason='quality replay copy',
                finished_at=current_timestamp
            where job_id=%s and node_key in ({placeholders})
              and status in ('pending', 'ready', 'stale')
            """,
            (job_id, *skipped_nodes),
        )


def delete_job(conn: DatabaseConnection, job_id: str) -> None:
    cursor = conn.execute("delete from jobs where id=%s", (job_id,))
    if cursor.rowcount == 0:
        raise ValueError("Job not found")


_RESUMABLE_JOB_STATUSES = {"paused"}


def resume_job(conn: DatabaseConnection, job_id: str) -> None:
    """Resume a job inside an active transaction.

    Only ``paused`` jobs may be resumed. The guard lives in the UPDATE
    predicate itself (same discipline as ``execution_pause.py``): the SELECT
    above only picks the branch and produces error messages — a concurrent
    run-to committing between SELECT and UPDATE turns the rowcount to 0
    instead of being overwritten with the stale read's values.
    """
    job = conn.execute(
        "select status, pause_reason from jobs where id=%s",
        (job_id,),
    ).fetchone()
    if job is None:
        raise ValueError("Job not found")
    if job["status"] not in _RESUMABLE_JOB_STATUSES:
        raise JobMutationConflict(
            "not_resumable",
            f"Job is {job['status']}, only paused jobs can be resumed",
        )
    pause_reason = str(job["pause_reason"] or "")
    if pause_reason == "target_reached":
        cursor = conn.execute(
            """
            update jobs
            set status='queued',
                execution_paused=0,
                execution_mode='full',
                target_node_key=null,
                pause_reason='',
                updated_at=current_timestamp
            where id=%s and status='paused' and pause_reason='target_reached'
            """,
            (job_id,),
        )
    else:
        cursor = conn.execute(
            """
            update jobs
            set status='queued',
                execution_paused=0,
                pause_reason='',
                updated_at=current_timestamp
            where id=%s and status='paused' and pause_reason=%s
            """,
            (job_id, pause_reason),
        )
    if cursor.rowcount == 0:
        # 守卫谓词不命中 = SELECT 与 UPDATE 之间状态被并发 mutation 改写
        # （典型：run-to 已提交并接管 execution_mode/target_node_key）。
        # 不落 SELECT 时的旧值，按冲突语义拒绝。
        current = conn.execute("select status from jobs where id=%s", (job_id,)).fetchone()
        if current is None:
            raise ValueError("Job not found")
        raise JobMutationConflict(
            "not_resumable",
            f"Job is {current['status']}, only paused jobs can be resumed",
        )
