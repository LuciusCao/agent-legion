"""Record a failed node plus its synthetic failed run for never-executed nodes.

Shared by the lease config-failure path and the Agent stale-definition
sweeper: both fail a queued node outside any execution, and both must leave
job_nodes/node_runs failure fields consistent (DB-FAILURE-001).
"""

from __future__ import annotations

from datetime import UTC, datetime

from server.app.db.connection import DatabaseConnection
from server.app.executors._lease_transactions import database_timestamp


def record_failed_node_without_execution(
    conn: DatabaseConnection,
    *,
    job_id: str,
    node_key: str,
    error_message: str,
    failure_category: str,
    failure_detail: str,
    log_path: str = "",
) -> int | None:
    """Fail a queued node and insert its synthetic failed node run.

    Returns the new node_runs id, or None when the node already left the
    queueable states (a concurrent claim/finish owns it now).
    """
    now_str = database_timestamp(datetime.now(UTC))
    cursor = conn.execute(
        """
        update job_nodes
        set status='failed', stale_reason='', error_message=%s, finished_at=%s,
            failure_category=%s, failure_detail=%s
        where job_id=%s and node_key=%s and status in ('pending', 'ready', 'stale')
        """,
        (error_message, now_str, failure_category, failure_detail, job_id, node_key),
    )
    if cursor.rowcount == 0:
        return None
    cursor = conn.execute(
        """
        insert into node_runs(
            job_id, node_key, status, command_json, log_path,
            run_dir, session_dir, started_at, finished_at, error_message,
            failure_category, failure_detail
        )
        values (%s, %s, 'failed', '[]', %s, '', '', %s, %s, %s, %s, %s)
        returning id
        """,
        (
            job_id,
            node_key,
            log_path,
            now_str,
            now_str,
            error_message,
            failure_category,
            failure_detail,
        ),
    )
    inserted = cursor.fetchone()
    if inserted is None:
        raise RuntimeError("node_runs insert did not return a row id")
    return int(inserted["id"])
