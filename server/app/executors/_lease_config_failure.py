"""Record configuration failures: a failed node run without a lease claim."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from server.app.db.connection import DatabaseConnection
from server.app.executors._lease_control import _sync_job_status
from server.app.executors._lease_transactions import _database_timestamp
from server.app.executors._path_canonicalization import canonicalize_data_path
from server.app.executors.models import ConfigurationFailureRequest
from server.app.services import failure_classification


def fail_without_lease(
    conn: DatabaseConnection,
    request: ConfigurationFailureRequest,
    error_message: str,
    data_dir: Path | None = None,
) -> int | None:
    """Record a failed node run without claiming a lease."""
    now = datetime.now(UTC)
    now_str = _database_timestamp(now)
    failure_category, failure_detail = failure_classification.resolve_failure_fields(
        "failed", None, error_message
    )
    cursor = conn.execute(
        """
        update job_nodes
        set status='failed', stale_reason='', error_message=?, finished_at=?,
            failure_category=?, failure_detail=?
        where job_id=? and node_key=? and status in ('pending', 'ready', 'stale')
        """,
        (
            error_message,
            now_str,
            failure_category,
            failure_detail,
            request.job_id,
            request.node_key,
        ),
    )
    if cursor.rowcount == 0:
        return None

    log_path = canonicalize_data_path(request.log_path, data_dir, "logs")
    cursor = conn.execute(
        """
        insert into node_runs(
            job_id, node_key, status, command_json, log_path,
            run_dir, session_dir, started_at, finished_at, error_message,
            failure_category, failure_detail
        )
        values (?, ?, 'failed', ?, ?, '', '', ?, ?, ?, ?, ?)
        returning id
        """,
        (
            request.job_id,
            request.node_key,
            json.dumps([]),
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
    node_run_id = int(inserted["id"])

    _sync_job_status(conn, request.job_id)
    return node_run_id
