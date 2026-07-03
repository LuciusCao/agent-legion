from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from server.app.db.connection import connect_sqlite


class JobMutationConflict(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class _AtomicMutationQueries(Protocol):
    path: Path


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


@contextmanager
def lease_guarded_mutation(
    path: Path,
    job_id: str,
    now: datetime,
    *,
    reject_running_nodes: bool,
) -> Iterator[sqlite3.Connection]:
    """Serialize a Job mutation with lease claims and validate busy state."""
    conn = connect_sqlite(path)
    conn.isolation_level = None
    try:
        conn.execute("begin immediate")
        active_lease = conn.execute(
            """
            select 1 from executor_leases
            where job_id=? and status='active' and expires_at>?
            limit 1
            """,
            (job_id, _timestamp(now)),
        ).fetchone()
        if active_lease is not None:
            raise JobMutationConflict("busy", "Job has an active executor lease")

        if reject_running_nodes:
            running_node = conn.execute(
                "select 1 from job_nodes where job_id=? and status='running' limit 1",
                (job_id,),
            ).fetchone()
            if running_node is not None:
                raise JobMutationConflict("busy", "Job has running nodes")

        yield conn
        conn.execute("commit")
    except Exception:
        if conn.in_transaction:
            conn.execute("rollback")
        raise
    finally:
        conn.close()


def apply_run_to(
    conn: sqlite3.Connection,
    job_id: str,
    target_node_key: str,
    closure: frozenset[str],
) -> None:
    target = conn.execute(
        "select status from job_nodes where job_id=? and node_key=?",
        (job_id, target_node_key),
    ).fetchone()
    if target is None:
        raise ValueError(f"Unknown job node: {job_id}.{target_node_key}")
    if target["status"] == "completed":
        raise JobMutationConflict("target_already_completed", "Target node is already completed")

    placeholders = ",".join("?" for _ in closure)
    if not placeholders:
        raise ValueError("Run-to closure cannot be empty")
    conn.execute(
        f"""
        update job_nodes
        set status='pending', stale_reason='', error_message='',
            started_at=null, finished_at=null, created_at=current_timestamp
        where job_id=? and node_key in ({placeholders}) and status != 'completed'
        """,
        (job_id, *sorted(closure)),
    )
    set_run_to_control(conn, job_id, target_node_key)


def set_run_to_control(
    conn: sqlite3.Connection,
    job_id: str,
    target_node_key: str,
) -> None:
    conn.execute(
        """
        update jobs
        set status='queued', execution_mode='until_node', target_node_key=?,
            execution_paused=0, pause_reason='', error_message='',
            updated_at=current_timestamp
        where id=?
        """,
        (target_node_key, job_id),
    )


def mark_nodes_for_rerun(
    conn: sqlite3.Connection,
    job_id: str,
    node_keys: Sequence[str],
    downstream_map: dict[str, list[str]],
) -> None:
    for node_key in node_keys:
        cursor = conn.execute(
            """
            update job_nodes
            set status='pending', stale_reason='', error_message='',
                started_at=null, finished_at=null, created_at=current_timestamp
            where job_id=? and node_key=?
            """,
            (job_id, node_key),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"Unknown job node: {job_id}.{node_key}")

    descendants = {
        descendant
        for node_key in node_keys
        for descendant in downstream_map.get(node_key, [])
        if descendant not in node_keys
    }
    for descendant in descendants:
        conn.execute(
            """
            update job_nodes
            set status='stale', stale_reason='upstream rerun', error_message='',
                created_at=current_timestamp
            where job_id=? and node_key=?
            """,
            (job_id, descendant),
        )
    conn.execute(
        """
        update jobs
        set status='queued', error_message='', updated_at=current_timestamp
        where id=?
        """,
        (job_id,),
    )


def delete_job(conn: sqlite3.Connection, job_id: str) -> None:
    cursor = conn.execute("delete from jobs where id=?", (job_id,))
    if cursor.rowcount == 0:
        raise ValueError("Job not found")


_RESUMABLE_JOB_STATUSES = {"paused"}


def resume_job(conn: sqlite3.Connection, job_id: str) -> None:
    """Resume a job inside an active transaction.

    Only ``paused`` jobs may be resumed. The status check and the state
    transition happen in the same transaction.
    """
    job = conn.execute(
        "select status, pause_reason from jobs where id=?",
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
            where id=?
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
            where id=?
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
    ) -> AbstractContextManager[sqlite3.Connection]:
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
        conn: sqlite3.Connection,
        job_id: str,
        node_keys: Sequence[str],
        downstream_map: dict[str, list[str]],
    ) -> None:
        mark_nodes_for_rerun(conn, job_id, node_keys, downstream_map)

    @staticmethod
    def set_run_to_control_in_transaction(
        conn: sqlite3.Connection,
        job_id: str,
        target_node_key: str,
    ) -> None:
        set_run_to_control(conn, job_id, target_node_key)

    @staticmethod
    def delete_job_in_transaction(conn: sqlite3.Connection, job_id: str) -> None:
        delete_job(conn, job_id)
