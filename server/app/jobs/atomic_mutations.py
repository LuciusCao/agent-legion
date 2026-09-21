from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from typing import Any, Protocol

from server.app.agent_broker.manifest_trim import cancel_queued_sql
from server.app.db.connection import DatabaseConnection
from server.app.db.rowmap import utc_datetime
from server.app.db.transaction import write_transaction
from server.app.jobs.job_state_mutations import JobMutationConflict, delete_job
from server.app.jobs.run_to_mutation import apply_run_to, set_run_to_control
from server.app.workflows.sharding import delete_shards

__all__ = [
    "AtomicJobMutationsMixin",
    "JobMutationConflict",
    "lease_guarded_mutation",
    "mark_nodes_for_rerun",
]


class _AtomicMutationQueries(Protocol):
    # #187 step 3: the backing DSN is private; atomic mutations are inside
    # the data layer, so they read the facade's own `_path` by convention.
    _path: str


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
    conn.execute(cancel_queued_sql(placeholders), (job_id, *sorted(affected_nodes)))
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
