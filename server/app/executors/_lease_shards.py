"""Shard-aware lease finish orchestration.

When a finished lease belongs to a shard execution (its ``execution_id`` was
recorded on a ``node_shards`` row at claim time), the shard row is updated
first and the aggregate state decides whether the owning ``job_nodes`` row
advances. Only terminal aggregates (``completed``/``failed``) touch the node
state machine — intermediate aggregates leave it running so in-flight shards
are not disturbed (Decision 3: shard rows are child execution records, the
node row stays the aggregate authority).
"""

from __future__ import annotations

from typing import Any

from server.app.db.connection import DatabaseConnection
from server.app.executors._lease_control import (
    _pause_job_on_target_completion,
    _sync_job_status,
)
from server.app.executors.models import ExecutionResult
from server.app.workflows.sharding import (
    ShardStatus,
    failed_shard_error,
    on_shard_finished,
    shard_index_for_execution,
)


def finish_shard_execution(
    conn: DatabaseConnection,
    lease: dict[str, Any],
    result: ExecutionResult,
    now_str: str,
) -> bool:
    """Advance shard + aggregate state for a shard lease; True when handled.

    Returns False for non-shard leases so the caller falls through to the
    regular node finish path.
    """
    shard_index = shard_index_for_execution(
        conn, lease["job_id"], lease["node_key"], lease["execution_id"]
    )
    if shard_index is None:
        return False
    status: ShardStatus = "completed" if result.status == "completed" else "failed"
    aggregate = on_shard_finished(
        conn,
        lease["job_id"],
        lease["node_key"],
        shard_index,
        status,
        output_json=result.output_json if status == "completed" else "",
        error_message=result.error_message,
    )
    if aggregate in ("completed", "failed"):
        error_message = failed_shard_error(conn, lease["job_id"], lease["node_key"])
        conn.execute(
            """
            update job_nodes
            set status=?, error_message=?, finished_at=?
            where job_id=? and node_key=?
            """,
            (aggregate, error_message, now_str, lease["job_id"], lease["node_key"]),
        )
        _sync_job_status(conn, lease["job_id"])
        if aggregate == "completed":
            _pause_job_on_target_completion(conn, lease["job_id"], lease["node_key"], now_str)
    return True
