from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from typing import Protocol

from server.app.db.connection import DatabaseConnection
from server.app.db.transaction import write_transaction
from server.app.workflows.sharding import delete_shards


class JobMutationConflict(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class _AtomicMutationQueries(Protocol):
    path: str


def _timestamp(value: datetime) -> datetime:
    return value.astimezone(UTC)


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
        active_lease = conn.execute(
            """
            select 1 from executor_leases
            where job_id=%s and status='active' and expires_at>%s
            limit 1
            """,
            (job_id, _timestamp(now)),
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
) -> None:
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
    conn.execute(
        f"""
        update job_nodes
        set status='pending', stale_reason='', error_message='',
            started_at=null, finished_at=null, created_at=current_timestamp
        where job_id=%s and node_key in ({placeholders}) and status != 'completed'
        """,
        (job_id, *sorted(closure)),
    )
    delete_shards(conn, job_id, closure)
    set_run_to_control(conn, job_id, target_node_key)


def set_run_to_control(
    conn: DatabaseConnection,
    job_id: str,
    target_node_key: str,
) -> None:
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


def mark_nodes_for_rerun(
    conn: DatabaseConnection,
    job_id: str,
    node_keys: Sequence[str],
    downstream_map: dict[str, list[str]],
) -> None:
    descendants = {
        descendant
        for node_key in node_keys
        for descendant in downstream_map.get(node_key, [])
        if descendant not in node_keys
    }
    affected_nodes = set(node_keys) | descendants
    placeholders = ",".join("%s" for _ in affected_nodes)
    conn.execute(
        f"""
        update node_runs
        set run_dir='', session_dir=''
        where job_id=%s and node_key in ({placeholders})
        """,
        (job_id, *sorted(affected_nodes)),
    )
    delete_shards(conn, job_id, affected_nodes)
    for node_key in node_keys:
        cursor = conn.execute(
            """
            update job_nodes
            set status='pending', stale_reason='', error_message='',
                failure_category='', failure_detail='',
                started_at=null, finished_at=null, created_at=current_timestamp
            where job_id=%s and node_key=%s
            """,
            (job_id, node_key),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"Unknown job node: {job_id}.{node_key}")
    for descendant in descendants:
        conn.execute(
            """
            update job_nodes
            set status='stale', stale_reason='upstream rerun', error_message='',
                failure_category='', failure_detail='',
                created_at=current_timestamp
            where job_id=%s and node_key=%s
            """,
            (job_id, descendant),
        )
    conn.execute(
        """
        update jobs
        set status='queued', error_message='', packed=0, updated_at=current_timestamp
        where id=%s
        """,
        (job_id,),
    )


def delete_job(conn: DatabaseConnection, job_id: str) -> None:
    cursor = conn.execute("delete from jobs where id=%s", (job_id,))
    if cursor.rowcount == 0:
        raise ValueError("Job not found")


_RESUMABLE_JOB_STATUSES = {"paused"}


def resume_job(conn: DatabaseConnection, job_id: str) -> None:
    """Resume a job inside an active transaction.

    Only ``paused`` jobs may be resumed. The status check and the state
    transition happen in the same transaction.
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
    if job["pause_reason"] == "target_reached":
        cursor = conn.execute(
            """
            update jobs
            set status='queued',
                execution_paused=0,
                execution_mode='full',
                target_node_key=null,
                pause_reason='',
                updated_at=current_timestamp
            where id=%s
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
            where id=%s
            """,
            (job_id,),
        )
    if cursor.rowcount == 0:
        raise ValueError("Job not found")


class AtomicJobMutationsMixin:
    def lease_guarded_mutation(
        self: _AtomicMutationQueries,
        job_id: str,
        now: datetime,
        *,
        reject_running_nodes: bool,
    ) -> AbstractContextManager[DatabaseConnection]:
        return lease_guarded_mutation(
            self.path,
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
            self.path,
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
    ) -> None:
        mark_nodes_for_rerun(conn, job_id, node_keys, downstream_map)

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
