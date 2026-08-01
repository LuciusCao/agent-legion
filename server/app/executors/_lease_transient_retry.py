"""Transient-failure retry at the lease finish path.

Split out of ``_lease_lifecycle.py`` so that module stays within its size
budget; mirrors the ``executors/_lease_*`` layout. Transient infrastructure
failures (see ``failure_classification.is_transient_retryable``) hand the
node back to the claimable set instead of failing the job; every attempt
stays recorded as its own failed node_run.
"""

from __future__ import annotations

from typing import Any

from server.app.db.connection import DatabaseConnection
from server.app.executors.models import ExecutionResult
from server.app.services import failure_classification

# Total attempts a transient failure gets before the node fails for good.
_MAX_TRANSIENT_ATTEMPTS = 3


def try_return_node_to_pending(
    conn: DatabaseConnection,
    lease: Any,
    result: ExecutionResult,
    failure_category: str,
    failure_detail: str,
) -> bool:
    """Reset the node to ``pending`` for a transient failure; False to fail it."""
    if result.status == "completed" or not failure_classification.is_transient_retryable(
        failure_detail
    ):
        return False
    row = conn.execute(
        "select count(*) as c from node_runs where job_id=? and node_key=? and status='failed'",
        (lease["job_id"], lease["node_key"]),
    ).fetchone()
    if row is None or int(row["c"]) >= _MAX_TRANSIENT_ATTEMPTS:
        return False
    conn.execute(
        """
        update job_nodes
        set status='pending', error_message=?, finished_at=null,
            failure_category=?, failure_detail=?
        where job_id=? and node_key=?
        """,
        (
            result.error_message,
            failure_category,
            failure_detail,
            lease["job_id"],
            lease["node_key"],
        ),
    )
    return True
