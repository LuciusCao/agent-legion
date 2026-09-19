"""Park a ready approval node at awaiting_approval — no lease, no node_run.

The workflow worker calls this instead of claiming when a ``type: approval``
node becomes ready (EXEC-APPROVAL-001). Waiting for a human is not an
execution, so no node_runs row is written; the decision history lives in
``approval_decisions``. The transition is guarded on the current status
inside the same transaction (idempotent across duplicate ready candidates
in one poll pass), and job status re-derives via ``sync_job_status``.

EXEC-GENERATION-001: the park transaction takes the per-job mutation
advisory lock and CAS-checks the candidate's epoch against
jobs.execution_generation (a reset since evaluation skips the park — the
next poll pass re-parks against the new epoch). A successful park stamps
the job_nodes row with the current epoch (park itself never bumps it), so
the later approve/reject decision can prove its target is not a pre-reset
leftover.
"""

from __future__ import annotations

import logging
from typing import Any

from server.app.db.connection import DatabaseConnection
from server.app.db.transaction import write_transaction
from server.app.executors._lease_control import (
    lock_job_mutation_and_read_generation,
    sync_job_status,
)
from server.app.workflows.approval_node import AWAITING_APPROVAL_STATUS

logger = logging.getLogger(__name__)


def park_awaiting_approval(
    conn: DatabaseConnection, job_id: str, node_key: str, *, execution_generation: int = 0
) -> bool:
    """Transition a runnable approval node to awaiting_approval.

    Returns False when the candidate's epoch is stale or the node is no
    longer in a runnable status (already parked, decided, or reset
    concurrently) — the caller treats that as "nothing to do", never an
    error.
    """
    current_generation = lock_job_mutation_and_read_generation(conn, job_id)
    if current_generation != execution_generation:
        logger.info(
            "approval park skipped (stale generation): job=%s node=%s expected=%s current=%s",
            job_id,
            node_key,
            execution_generation,
            current_generation,
        )
        return False
    cursor = conn.execute(
        """
        update job_nodes
        set status=%s, stale_reason='', error_message='',
            started_at=current_timestamp, finished_at=null,
            execution_generation=%s
        where job_id=%s and node_key=%s and status in ('pending', 'ready', 'stale')
        """,
        (AWAITING_APPROVAL_STATUS, current_generation, job_id, node_key),
    )
    if cursor.rowcount == 0:
        return False
    sync_job_status(conn, job_id)
    return True


def park_awaiting_approval_repo(
    repo: Any, job_id: str, node_key: str, *, execution_generation: int = 0
) -> bool:
    """Repository write path: one transaction per park (leases.py delegate)."""
    with write_transaction(repo.path) as conn:
        return park_awaiting_approval(
            conn, job_id, node_key, execution_generation=execution_generation
        )
