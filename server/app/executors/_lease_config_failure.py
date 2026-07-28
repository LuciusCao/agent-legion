"""Record configuration failures: a failed node run without a lease claim."""

from __future__ import annotations

from pathlib import Path

from server.app.db.connection import DatabaseConnection
from server.app.executors._failed_node_recording import record_failed_node_without_execution
from server.app.executors._lease_control import _sync_job_status
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
    failure_category, failure_detail = failure_classification.resolve_failure_fields(
        "failed", None, error_message
    )
    node_run_id = record_failed_node_without_execution(
        conn,
        job_id=request.job_id,
        node_key=request.node_key,
        error_message=error_message,
        failure_category=failure_category,
        failure_detail=failure_detail,
        log_path=canonicalize_data_path(request.log_path, data_dir, "logs"),
    )
    if node_run_id is None:
        return None
    _sync_job_status(conn, request.job_id)
    return node_run_id
