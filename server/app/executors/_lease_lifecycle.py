from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from server.app.executors._lease_control import (
    _pause_job_on_target_completion,
    _sync_job_status,
)
from server.app.executors._lease_transactions import _sqlite_timestamp
from server.app.executors._path_canonicalization import (
    canonicalize_data_path,
    canonicalize_finish_paths,
)
from server.app.executors.models import ConfigurationFailureRequest, ExecutionResult
from server.app.services.token_usage_capture import capture_and_persist_token_usage_for_lease


def heartbeat_lease(conn: sqlite3.Connection, lease_id: str, ttl_seconds: int) -> bool:
    now = datetime.now(UTC)
    lease = conn.execute(
        "select status from executor_leases where id=?",
        (lease_id,),
    ).fetchone()
    if lease is None or lease["status"] != "active":
        return False
    expires_at = now + timedelta(seconds=ttl_seconds)
    conn.execute(
        """
        update executor_leases
        set heartbeat_at=?, expires_at=?
        where id=? and status='active'
        """,
        (_sqlite_timestamp(now), _sqlite_timestamp(expires_at), lease_id),
    )
    return True


def finish_lease(
    conn: sqlite3.Connection, lease_id: str, result: ExecutionResult, data_dir: Path | None = None
) -> bool:
    now = datetime.now(UTC)
    now_str = _sqlite_timestamp(now)
    lease = conn.execute("select * from executor_leases where id=?", (lease_id,)).fetchone()
    if lease is None or lease["status"] != "active":
        return False

    conn.execute("update executor_leases set status='released' where id=?", (lease_id,))

    node_run = conn.execute(
        "select log_path from node_runs where id=?", (lease["node_run_id"],)
    ).fetchone()
    effective_log_path, run_dir, session_dir = canonicalize_finish_paths(
        result,
        data_dir,
        node_run["log_path"] if node_run is not None else "",
        lease["node_key"],
        lease["job_id"],
    )
    conn.execute(
        """
        update node_runs
        set status=?, exit_code=?, error_message=?,
            command_json=?, log_path=?, run_dir=?, session_dir=?,
            skill_version=?, finished_at=?
        where id=?
        """,
        (
            result.status,
            result.exit_code,
            result.error_message,
            json.dumps(list(result.command)),
            effective_log_path,
            run_dir,
            session_dir,
            result.skill_version,
            now_str,
            lease["node_run_id"],
        ),
    )
    conn.execute(
        """
        update job_nodes
        set status=?, error_message=?, finished_at=?
        where job_id=? and node_key=?
        """,
        (
            "completed" if result.status == "completed" else "failed",
            result.error_message,
            now_str,
            lease["job_id"],
            lease["node_key"],
        ),
    )
    _sync_job_status(conn, lease["job_id"])

    if data_dir is not None and result.status in ("completed", "failed"):
        try:
            capture_and_persist_token_usage_for_lease(conn, lease, data_dir)
        except Exception:
            logging.getLogger(__name__).debug(
                "Failed to capture token usage for lease %s", lease_id, exc_info=True
            )

    if result.status == "completed":
        _pause_job_on_target_completion(conn, lease["job_id"], lease["node_key"], now_str)

    return True


def fail_without_lease(
    conn: sqlite3.Connection,
    request: ConfigurationFailureRequest,
    error_message: str,
    data_dir: Path | None = None,
) -> int | None:
    """Record a failed node run without claiming a lease."""
    now = datetime.now(UTC)
    now_str = _sqlite_timestamp(now)
    cursor = conn.execute(
        """
        update job_nodes
        set status='failed', stale_reason='', error_message=?, finished_at=?
        where job_id=? and node_key=? and status in ('pending', 'ready', 'stale')
        """,
        (error_message, now_str, request.job_id, request.node_key),
    )
    if cursor.rowcount == 0:
        return None

    log_path = canonicalize_data_path(request.log_path, data_dir, "logs")
    cursor = conn.execute(
        """
        insert into node_runs(
            job_id, node_key, status, command_json, log_path,
            run_dir, session_dir, started_at, finished_at, error_message
        )
        values (?, ?, 'failed', ?, ?, '', '', ?, ?, ?)
        """,
        (
            request.job_id,
            request.node_key,
            json.dumps([]),
            log_path,
            now_str,
            now_str,
            error_message,
        ),
    )
    node_run_id = cursor.lastrowid

    _sync_job_status(conn, request.job_id)
    return node_run_id


def expire_stale_leases(conn: sqlite3.Connection, now: datetime) -> list[str]:
    now_str = _sqlite_timestamp(now)
    rows = conn.execute(
        """
        select id, job_id, node_key, node_run_id
        from executor_leases
        where status='active' and expires_at<=?
        """,
        (now_str,),
    ).fetchall()
    expired: list[str] = []
    for row in rows:
        expired.append(row["id"])
        conn.execute(
            "update executor_leases set status='expired' where id=?",
            (row["id"],),
        )
        conn.execute(
            """
            update node_runs
            set status='failed', error_message='lease expired', finished_at=?
            where id=?
            """,
            (now_str, row["node_run_id"]),
        )
        conn.execute(
            """
            update job_nodes
            set status='failed', stale_reason='', error_message='lease expired', finished_at=?
            where job_id=? and node_key=?
            """,
            (now_str, row["job_id"], row["node_key"]),
        )
        _sync_job_status(conn, row["job_id"])
        conn.execute(
            """
            update jobs
            set status='failed', updated_at=?
            where id=? and status != 'failed'
            """,
            (now_str, row["job_id"]),
        )
    return expired
