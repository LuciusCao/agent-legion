from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from typing import Any, Protocol

from server.app.agent_broker.manifest_trim import MANIFEST_TRIM
from server.app.db.connection import DatabaseConnection
from server.app.db.rowmap import utc_datetime
from server.app.db.transaction import write_transaction
from server.app.jobs.job_state_mutations import JobMutationConflict, delete_job
from server.app.workflows.sharding import delete_shards

__all__ = [
    "AtomicJobMutationsMixin",
    "JobMutationConflict",
    "apply_run_to",
    "lease_guarded_mutation",
    "mark_nodes_for_rerun",
    "set_run_to_control",
]


class _AtomicMutationQueries(Protocol):
    # #187 step 3: the backing DSN is private; atomic mutations are inside
    # the data layer, so they read the facade's own `_path` by convention.
    _path: str


def _cancel_queued_sql(placeholders: str) -> str:
    """Rerun-path cancel SQL; slims manifests in the same statement (#142/#354)."""
    return (
        "update agent_execution_requests set state='cancelled', finished_at=current_timestamp,"
        f" manifest_json={MANIFEST_TRIM} where job_id=%s and node_key in ({placeholders})"
        " and state='queued'"
    )


@contextmanager
def lease_guarded_mutation(
    path: str,
    job_id: str,
    now: datetime,
    *,
    reject_running_nodes: bool,
) -> Iterator[DatabaseConnection]:
    """Serialize a Job mutation with lease claims and validate busy state."""
    with write_transaction(path) as conn:
        # EXEC-GENERATION-001：全库统一的 per-job 锁域，事务首句获取。
        # 锁序：池级锁（code-pool/agent-ws/agent-worker）→
        # job-mutation:<job_id> → 行锁；mutation 侧只取本锁，不取池级锁。
        conn.execute("select pg_advisory_xact_lock(hashtext('job-mutation:' || %s))", (job_id,))
        active_lease = conn.execute(
            """
            select 1 from executor_leases
            where job_id=%s and status='active' and expires_at>%s
            limit 1
            """,
            (job_id, utc_datetime(now)),
        ).fetchone()
        if active_lease is not None:
            raise JobMutationConflict("busy", "Job has an active executor lease")

        if reject_running_nodes:
            running_node = conn.execute(
                "select 1 from job_nodes where job_id=%s and status='running' limit 1",
                (job_id,),
            ).fetchone()
            if running_node is not None:
                raise JobMutationConflict("busy", "Job has running nodes")

        yield conn


def apply_run_to(
    conn: DatabaseConnection,
    job_id: str,
    target_node_key: str,
    closure: frozenset[str],
    *,
    reset_nodes: Sequence[str] | None = None,
    staged_artifact_names: frozenset[str] | set[str] = frozenset(),
) -> list[dict[str, Any]]:
    target = conn.execute(
        "select status from job_nodes where job_id=%s and node_key=%s",
        (job_id, target_node_key),
    ).fetchone()
    if target is None:
        raise ValueError(f"Unknown job node: {job_id}.{target_node_key}")
    if target["status"] == "completed":
        raise JobMutationConflict("target_already_completed", "Target node is already completed")

    placeholders = ",".join("%s" for _ in closure)
    if not placeholders:
        raise ValueError("Run-to closure cannot be empty")
    # 正规化：调用方（或测试替身）给的任何可迭代都收敛成集合再判空，
    # 空集合必须跳过清单删除——空 join 会生成 name in () 语法错误。
    staged_artifact_names = frozenset(staged_artifact_names)
    deleted_rows: list[dict[str, Any]] = []
    if staged_artifact_names and reset_nodes:
        # #759：与 mark_nodes_for_rerun 同 invariant——被暂存产物（本地文件
        # 已移走）的清单行必须在同事务删除，否则 run-to 永不完成时作业仍在
        # 从对象存储提供上一轮产物。node 过滤用调用方算出的权威重置集
        # （closure ∩ 非 completed），与 stage_outputs 的暂存集同源。
        reset_marks = ",".join("%s" for _ in reset_nodes)
        name_marks = ",".join("%s" for _ in staged_artifact_names)
        deleted_rows = [
            dict(row)
            for row in conn.execute(
                f"""
                delete from job_artifacts
                where job_id=%s and node_key in ({reset_marks}) and name in ({name_marks})
                returning node_key, name, storage_key
                """,
                (job_id, *sorted(reset_nodes), *sorted(staged_artifact_names)),
            ).fetchall()
        ]
    # EXEC-GENERATION-001：run-to（无起始节点）路径的唯一 bump 点，fold 进
    # set_run_to_control 的 jobs UPDATE（run-to-with-start 在同事务里改走
    # mark_nodes_for_rerun 的 jobs UPDATE bump，这里不再 bump，整事务恰好一次）。
    generation = set_run_to_control(conn, job_id, target_node_key, bump_generation=True)
    conn.execute(
        f"""
        update job_nodes
        set status='pending', stale_reason='', error_message='',
            started_at=null, finished_at=null, created_at=current_timestamp,
            execution_generation=%s
        where job_id=%s and node_key in ({placeholders}) and status != 'completed'
        """,
        (generation, job_id, *sorted(closure)),
    )
    # 已入队的 queued agent 请求不复查上游，重置节点前必须取消（见 mark_nodes_for_rerun）。
    conn.execute(_cancel_queued_sql(placeholders), (job_id, *sorted(closure)))
    # #759：分片行删除与节点重置同一集合——按全 closure 删会把保持
    # completed 的分片节点的 output_json 永久抹掉（reduce 重跑拼出空输入）。
    delete_shards(conn, job_id, reset_nodes if reset_nodes is not None else closure)
    return deleted_rows


def set_run_to_control(
    conn: DatabaseConnection,
    job_id: str,
    target_node_key: str,
    *,
    bump_generation: bool = False,
) -> int | None:
    """Write the run-to execution control row; optionally bump the epoch.

    ``bump_generation=True``（仅 ``apply_run_to``）把 EXEC-GENERATION-001 的
    代次 +1 fold 进同一条 jobs UPDATE 并返回新代次，供调用方给重置的
    job_nodes 行盖戳；默认 False 供 run-to-with-start 使用——同事务的
    bump 已由 mark_nodes_for_rerun 承担，这里再 bump 就是双重 +1。
    """
    if not bump_generation:
        conn.execute(
            """
            update jobs
            set status='queued', execution_mode='until_node', target_node_key=%s,
                execution_paused=0, pause_reason='', error_message='',
                updated_at=current_timestamp
            where id=%s
            """,
            (target_node_key, job_id),
        )
        return None
    row = conn.execute(
        """
        update jobs
        set status='queued', execution_mode='until_node', target_node_key=%s,
            execution_paused=0, pause_reason='', error_message='',
            execution_generation=execution_generation+1,
            updated_at=current_timestamp
        where id=%s
        returning execution_generation
        """,
        (target_node_key, job_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"Job not found: {job_id}")
    return int(row["execution_generation"])


def mark_nodes_for_rerun(
    conn: DatabaseConnection,
    job_id: str,
    node_keys: Sequence[str],
    downstream_map: dict[str, list[str]],
    *,
    staged_artifact_names: frozenset[str] | set[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Reset rerun targets to pending (descendants to stale) and delete the
    affected nodes' object-storage manifest rows (#508).

    ``staged_artifact_names`` is the set ``stage_outputs`` staged for the same
    closure (outputs minus RMW): exactly the artifacts whose local files the
    rerun removed, so their ``job_artifacts`` rows must go in the SAME
    transaction — otherwise a rerun that never completes leaves the job
    listing (and serving) the previous run's artifacts from object storage.
    Bumps ``jobs.execution_generation`` once (EXEC-GENERATION-001) and
    stamps the reset node rows with the new epoch.
    Returns the deleted manifest rows (with ``storage_key``) for the caller's
    post-commit best-effort object deletion.
    """
    descendants = {
        descendant
        for node_key in node_keys
        for descendant in downstream_map.get(node_key, [])
        if descendant not in node_keys
    }
    affected_nodes = set(node_keys) | descendants
    placeholders = ",".join("%s" for _ in affected_nodes)
    deleted_rows: list[dict[str, Any]] = []
    if staged_artifact_names:
        name_marks = ",".join("%s" for _ in staged_artifact_names)
        deleted_rows = [
            dict(row)
            for row in conn.execute(
                f"""
                delete from job_artifacts
                where job_id=%s and node_key in ({placeholders}) and name in ({name_marks})
                returning node_key, name, storage_key
                """,
                (job_id, *sorted(affected_nodes), *sorted(staged_artifact_names)),
            ).fetchall()
        ]
    conn.execute(
        f"""
        update node_runs
        set run_dir='', session_dir=''
        where job_id=%s and node_key in ({placeholders})
        """,
        (job_id, *sorted(affected_nodes)),
    )
    delete_shards(conn, job_id, affected_nodes)
    # EXEC-GENERATION-001：rerun / approval rework / run-to-with-start 共用
    # 的唯一 bump 点——代次 +1 fold 进本条 jobs UPDATE（原子），returning
    # 拿新代次，给下面重置的 job_nodes 行（pending 目标 + stale 下游）盖
    # 同一戳；未被重置的节点行不动。
    bumped = conn.execute(
        """
        update jobs
        set status='queued', error_message='', packed=0,
            execution_generation=execution_generation+1, updated_at=current_timestamp
        where id=%s returning execution_generation
        """,
        (job_id,),
    ).fetchone()
    if bumped is None:
        raise ValueError(f"Job not found: {job_id}")
    generation = int(bumped["execution_generation"])
    for node_key in node_keys:
        cursor = conn.execute(
            """
            update job_nodes
            set status='pending', stale_reason='', error_message='',
                failure_category='', failure_detail='',
                started_at=null, finished_at=null, created_at=current_timestamp,
                execution_generation=%s
            where job_id=%s and node_key=%s
            """,
            (generation, job_id, node_key),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"Unknown job node: {job_id}.{node_key}")
    for descendant in descendants:
        conn.execute(
            """
            update job_nodes
            set status='stale', stale_reason='upstream rerun', error_message='',
                failure_category='', failure_detail='',
                created_at=current_timestamp,
                execution_generation=%s
            where job_id=%s and node_key=%s
            """,
            (generation, job_id, descendant),
        )
    # rerun 前合法入队的 queued agent 请求在 claim 侧只复查节点自身状态
    # （stale 会放行），不复查上游；rerun 又已删除下游产出，不取消就会在
    # 输入未重生成前抢跑（generate_possible_errors 缺输入失败事故）。
    # claimed/reporting 的请求持有 active lease，lease_guarded_mutation 已拦。
    conn.execute(_cancel_queued_sql(placeholders), (job_id, *sorted(affected_nodes)))
    return deleted_rows


class AtomicJobMutationsMixin:
    def lease_guarded_mutation(
        self: _AtomicMutationQueries,
        job_id: str,
        now: datetime,
        *,
        reject_running_nodes: bool,
    ) -> AbstractContextManager[DatabaseConnection]:
        return lease_guarded_mutation(
            self._path,
            job_id,
            now,
            reject_running_nodes=reject_running_nodes,
        )

    def apply_run_to_atomic(
        self: _AtomicMutationQueries,
        job_id: str,
        target_node_key: str,
        closure: frozenset[str],
        *,
        now: datetime | None = None,
    ) -> None:
        with lease_guarded_mutation(
            self._path,
            job_id,
            now or datetime.now(UTC),
            reject_running_nodes=True,
        ) as conn:
            apply_run_to(conn, job_id, target_node_key, closure)

    @staticmethod
    def mark_nodes_for_rerun_in_transaction(
        conn: DatabaseConnection,
        job_id: str,
        node_keys: Sequence[str],
        downstream_map: dict[str, list[str]],
        *,
        staged_artifact_names: frozenset[str] | set[str] = frozenset(),
    ) -> list[dict[str, Any]]:
        return mark_nodes_for_rerun(
            conn, job_id, node_keys, downstream_map, staged_artifact_names=staged_artifact_names
        )

    @staticmethod
    def set_run_to_control_in_transaction(
        conn: DatabaseConnection,
        job_id: str,
        target_node_key: str,
    ) -> None:
        set_run_to_control(conn, job_id, target_node_key)

    @staticmethod
    def delete_job_in_transaction(conn: DatabaseConnection, job_id: str) -> None:
        delete_job(conn, job_id)
