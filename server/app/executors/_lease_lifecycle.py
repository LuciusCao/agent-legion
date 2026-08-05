from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from server.app.db.connection import DatabaseConnection
from server.app.executors._lease_control import (
    _pause_job_on_target_completion,
    sync_job_status,
)
from server.app.executors._lease_shards import finish_shard_execution
from server.app.executors._lease_transactions import database_timestamp
from server.app.executors._lease_transient_retry import try_return_node_to_pending
from server.app.executors._path_canonicalization import canonicalize_finish_paths
from server.app.executors.models import ExecutionResult
from server.app.services import failure_classification
from server.app.workflows.sharding import (
    failed_shard_error,
    on_shard_finished,
    shard_index_for_execution,
)


def heartbeat_lease(conn: DatabaseConnection, lease_id: str, ttl_seconds: int) -> bool:
    now = datetime.now(UTC)
    lease = conn.execute(
        "select status from executor_leases where id=%s",
        (lease_id,),
    ).fetchone()
    if lease is None or lease["status"] != "active":
        return False
    expires_at = now + timedelta(seconds=ttl_seconds)
    conn.execute(
        """
        update executor_leases
        set heartbeat_at=%s, expires_at=%s
        where id=%s and status='active'
        """,
        (database_timestamp(now), database_timestamp(expires_at), lease_id),
    )
    return True


def finish_lease(
    conn: DatabaseConnection, lease_id: str, result: ExecutionResult, data_dir: Path | None = None
) -> bool:
    now = datetime.now(UTC)
    now_str = database_timestamp(now)
    lease = conn.execute("select * from executor_leases where id=%s", (lease_id,)).fetchone()
    if lease is None or lease["status"] != "active":
        return False

    conn.execute("update executor_leases set status='released' where id=%s", (lease_id,))

    node_run = conn.execute(
        "select log_path from node_runs where id=%s", (lease["node_run_id"],)
    ).fetchone()
    effective_log_path, run_dir, session_dir = canonicalize_finish_paths(
        result,
        data_dir,
        node_run["log_path"] if node_run is not None else "",
        lease["node_key"],
        lease["job_id"],
    )
    failure_category, failure_detail = failure_classification.classify_execution_result(result)
    conn.execute(
        """
        update node_runs
        set status=%s, exit_code=%s, error_message=%s, failure_category=%s, failure_detail=%s,
            command_json=%s, log_path=%s, run_dir=%s, session_dir=%s,
            skill_version=%s, finished_at=%s, runner=%s
        where id=%s
        """,
        (
            result.status,
            result.exit_code,
            result.error_message,
            failure_category,
            failure_detail,
            json.dumps(list(result.command)),
            effective_log_path,
            run_dir,
            session_dir,
            result.skill_version,
            now_str,
            result.runner or lease["executor_id"],
            lease["node_run_id"],
        ),
    )
    if finish_shard_execution(conn, lease, result, now_str):
        return True

    if try_return_node_to_pending(conn, lease, result, failure_category, failure_detail):
        sync_job_status(conn, lease["job_id"])
        return True

    conn.execute(
        """
        update job_nodes
        set status=%s, error_message=%s, finished_at=%s, failure_category=%s, failure_detail=%s
        where job_id=%s and node_key=%s
        """,
        (
            "completed" if result.status == "completed" else "failed",
            result.error_message,
            now_str,
            failure_category,
            failure_detail,
            lease["job_id"],
            lease["node_key"],
        ),
    )
    sync_job_status(conn, lease["job_id"])

    if result.status == "completed":
        _pause_job_on_target_completion(conn, lease["job_id"], lease["node_key"], now_str)

    return True


def expire_stale_leases(conn: DatabaseConnection, now: datetime) -> list[str]:
    now_str = database_timestamp(now)
    # Agent Worker leases ('agent:%') are owned by the Agent broker sweep
    # (requeue-with-retry semantics); expiring them here would fail the node
    # and job while the broker later requeues the request, leaving the job
    # failed with a permanently queued request.
    rows = conn.execute(
        """
        select id, job_id, node_key, node_run_id, execution_id
        from executor_leases
        where status='active' and expires_at<=%s and not starts_with(executor_id, 'agent:')
        """,
        (now_str,),
    ).fetchall()
    expired: list[str] = []
    for row in rows:
        if _expire_lease_row(conn, row, now_str):
            expired.append(row["id"])
    return expired


def _expire_lease_row(conn: DatabaseConnection, row: dict[str, Any], now_str: str) -> bool:
    """Expire one stale lease row; False when it left the stale set concurrently.

    The guard predicates are re-evaluated by PostgreSQL against the newest
    committed row version when a concurrent finish/heartbeat touched the row
    after this transaction's SELECT, so a lease that was released or renewed
    in between is left untouched instead of being clobbered to 'expired'.
    """
    cursor = conn.execute(
        """
        update executor_leases set status='expired'
        where id=%s and status='active' and expires_at<=%s
        """,
        (row["id"], now_str),
    )
    if cursor.rowcount == 0:
        return False
    conn.execute(
        """
        update node_runs
        set status='failed', error_message='lease expired', finished_at=%s
        where id=%s
        """,
        (now_str, row["node_run_id"]),
    )
    shard_index = shard_index_for_execution(
        conn,
        str(row["job_id"]),
        str(row["node_key"]),
        str(row["execution_id"]),
    )
    if shard_index is not None:
        aggregate = on_shard_finished(
            conn,
            str(row["job_id"]),
            str(row["node_key"]),
            shard_index,
            "failed",
            error_message="lease expired",
        )
        if aggregate in ("completed", "failed"):
            error_message = failed_shard_error(
                conn,
                str(row["job_id"]),
                str(row["node_key"]),
            )
            conn.execute(
                """
                update job_nodes
                set status=%s, stale_reason='', error_message=%s, finished_at=%s
                where job_id=%s and node_key=%s
                """,
                (
                    aggregate,
                    error_message,
                    now_str,
                    row["job_id"],
                    row["node_key"],
                ),
            )
            sync_job_status(conn, str(row["job_id"]))
        return True
    conn.execute(
        """
        update job_nodes
        set status='failed', stale_reason='', error_message='lease expired', finished_at=%s
        where job_id=%s and node_key=%s
        """,
        (now_str, row["job_id"], row["node_key"]),
    )
    sync_job_status(conn, row["job_id"])
    conn.execute(
        """
        update jobs
        set status='failed', updated_at=%s
        where id=%s and status != 'failed'
        """,
        (now_str, row["job_id"]),
    )
    return True
