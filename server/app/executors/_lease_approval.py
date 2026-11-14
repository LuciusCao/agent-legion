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
next poll pass re-parks against the new epoch). The same lock also re-reads
execution control: paused or terminal jobs and stale mode/target scopes skip
the write. A successful park stamps the job_nodes row with the current epoch
(provenance for which epoch parked it; park itself never bumps it). Decisions on the parked
gate are guarded by status alone (``_guard_awaiting``): a reset of the
gate flips it out of awaiting_approval before any decision can land,
while a bump from an untouched sibling branch must not brick the gate.
"""

from __future__ import annotations

from typing import Any

from server.app.db.connection import DatabaseConnection
from server.app.executors._approval_control import lock_and_validate_approval_park
from server.app.executors._lease_control import sync_job_status
from server.app.workflows.approval_node import AWAITING_APPROVAL_STATUS


def park_awaiting_approval(
    conn: DatabaseConnection,
    job_id: str,
    node_key: str,
    *,
    execution_generation: int = 0,
    control_snapshot: dict[str, Any] | None = None,
    allowed_node_keys: frozenset[str] | None = None,
) -> bool:
    """Transition a runnable approval node to awaiting_approval.

    Returns False when the candidate's epoch is stale or the node is no
    longer in a runnable status (already parked, decided, or reset
    concurrently) — the caller treats that as "nothing to do", never an
    error.
    """
    current = lock_and_validate_approval_park(
        conn,
        job_id,
        node_key,
        execution_generation=execution_generation,
        control_snapshot=control_snapshot,
        allowed_node_keys=allowed_node_keys,
    )
    if current is None:
        return False
    current_generation = int(current["execution_generation"])
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
