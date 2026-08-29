"""Park a ready approval node at awaiting_approval — no lease, no node_run.

The workflow worker calls this instead of claiming when a ``type: approval``
node becomes ready (EXEC-APPROVAL-001). Waiting for a human is not an
execution, so no node_runs row is written; the decision history lives in
``approval_decisions``. The transition is guarded on the current status
inside the same transaction (idempotent across duplicate ready candidates
in one poll pass), and job status re-derives via ``sync_job_status``.
"""

from __future__ import annotations

from typing import Any

from server.app.db.connection import DatabaseConnection
from server.app.db.transaction import write_transaction
from server.app.executors._lease_control import sync_job_status
from server.app.workflows.approval_node import AWAITING_APPROVAL_STATUS


def park_awaiting_approval(conn: DatabaseConnection, job_id: str, node_key: str) -> bool:
    """Transition a runnable approval node to awaiting_approval.

    Returns False when the node is no longer in a runnable status (already
    parked, decided, or reset concurrently) — the caller treats that as
    "nothing to do", never an error.
    """
    cursor = conn.execute(
        """
        update job_nodes
        set status=%s, stale_reason='', error_message='',
            started_at=current_timestamp, finished_at=null
        where job_id=%s and node_key=%s and status in ('pending', 'ready', 'stale')
        """,
        (AWAITING_APPROVAL_STATUS, job_id, node_key),
    )
    if cursor.rowcount == 0:
        return False
    sync_job_status(conn, job_id)
    return True


def park_awaiting_approval_repo(repo: Any, job_id: str, node_key: str) -> bool:
    """Repository write path: one transaction per park (leases.py delegate)."""
    with write_transaction(repo.path) as conn:
        return park_awaiting_approval(conn, job_id, node_key)
