from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from server.app.db.connection import DatabaseConnection
from server.app.executors._lease_transactions import _database_timestamp
from server.app.executors.models import LeaseClaimRequest


def _read_job_execution_control(conn: DatabaseConnection, job_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        select execution_mode, target_node_key, execution_paused, pause_reason
        from jobs
        where id=?
        """,
        (job_id,),
    ).fetchone()
    if row is None:
        return {
            "execution_mode": "full",
            "target_node_key": None,
            "execution_paused": False,
            "pause_reason": "",
        }
    return {
        "execution_mode": row["execution_mode"],
        "target_node_key": row["target_node_key"],
        "execution_paused": bool(row["execution_paused"]),
        "pause_reason": row["pause_reason"],
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


def _sync_job_status(conn: DatabaseConnection, job_id: str) -> None:
    still_running = conn.execute(
        "select 1 from job_nodes where job_id=? and status='running'",
        (job_id,),
    ).fetchone()
    if still_running is not None:
        conn.execute(
            "update jobs set status=?, updated_at=? where id=?",
            ("running", _database_timestamp(datetime.now(UTC)), job_id),
        )
        return

    any_failed = conn.execute(
        "select 1 from job_nodes where job_id=? and status='failed'",
        (job_id,),
    ).fetchone()
    if any_failed is not None:
        conn.execute(
            "update jobs set status=?, updated_at=? where id=?",
            ("failed", _database_timestamp(datetime.now(UTC)), job_id),
        )
        return

    paused = conn.execute(
        "select 1 from jobs where id=? and execution_paused=1",
        (job_id,),
    ).fetchone()
    if paused is not None:
        conn.execute(
            "update jobs set status=?, updated_at=? where id=?",
            ("paused", _database_timestamp(datetime.now(UTC)), job_id),
        )
        return

    non_terminal = conn.execute(
        """
        select 1 from job_nodes
        where job_id=? and status not in ('completed', 'not_applicable')
        """,
        (job_id,),
    ).fetchone()
    if non_terminal is not None:
        conn.execute(
            "update jobs set status=?, updated_at=? where id=?",
            ("queued", _database_timestamp(datetime.now(UTC)), job_id),
        )
        return

    conn.execute(
        "update jobs set status=?, updated_at=? where id=?",
        ("completed", _database_timestamp(datetime.now(UTC)), job_id),
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
        where id=?
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
                updated_at=?
            where id=?
            """,
            (now_str, job_id),
        )


def active_lease_counts(conn: DatabaseConnection, executor_id: str) -> dict[str, int]:
    now_str = _database_timestamp(datetime.now(UTC))
    counts: dict[str, int] = {"global": 0}

    allocated = conn.execute(
        "select workspace_id from workspace_executor_allocations where executor_id=?",
        (executor_id,),
    ).fetchall()
    for row in allocated:
        counts[row["workspace_id"]] = 0

    global_row = conn.execute(
        """
        select count(*) as cnt
        from executor_leases
        where executor_id=? and status='active' and expires_at>?
        """,
        (executor_id, now_str),
    ).fetchone()
    counts["global"] = int(global_row["cnt"]) if global_row is not None else 0

    rows = conn.execute(
        """
        select workspace_id, count(*) as cnt
        from executor_leases
        where executor_id=? and status='active' and expires_at>?
        group by workspace_id
        """,
        (executor_id, now_str),
    ).fetchall()
    for row in rows:
        counts[row["workspace_id"]] = row["cnt"]
    return counts
