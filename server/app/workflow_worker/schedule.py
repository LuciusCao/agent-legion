"""Lease claiming for the workflow worker's ready candidates.

Extracted from the worker thread to keep it within its size budget; the
single-candidate dispatch decision lives in ``claim_submit`` (same budget
split). The capacity snapshot checks there are optimization hints that skip
pointless ``try_claim`` write-lock acquisitions; the lease claim transaction
remains the authoritative capacity enforcement. Ready candidates are
collected once per poll pass by ``server.app.workflow_worker.ready``.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Any

from server.app.executors.scheduling.capacity import CapacitySnapshot
from server.app.workflow_worker.claim_submit import try_claim_and_submit

if TYPE_CHECKING:
    from server.app.workflow_worker.ready import ReadyCandidate
    from server.app.workflow_worker.thread import WorkflowWorkerThread


def claim_ready_queues(
    worker: WorkflowWorkerThread,
    workspaces: dict[str, dict[str, Any]],
    queues: dict[str, deque[ReadyCandidate]],
    snapshot: CapacitySnapshot,
) -> int:
    """Drain the ready queues round-robin: one claim per workspace per round.

    Returns the number of submitted claims. Candidates whose claim fails are
    dropped for this pass; the next poll pass re-evaluates them from fresh
    state.
    """
    claims = 0
    while queues:
        round_claimed = False
        for workspace_id in worker.state.round_robin.order(list(queues)):
            queue = queues.get(workspace_id)
            if queue is None or worker._is_paused(workspace_id):
                continue
            if claim_next_candidate(worker, workspaces[workspace_id], queue, snapshot):
                round_claimed = True
                claims += 1
                worker.state.round_robin.complete_pass(workspace_id)
            if not queue:
                del queues[workspace_id]
        if not round_claimed:
            break
    return claims


def claim_next_candidate(
    worker: WorkflowWorkerThread,
    workspace: dict[str, Any],
    candidates: deque[ReadyCandidate],
    snapshot: CapacitySnapshot,
) -> bool:
    """Pop candidates until one claim is submitted; return True on a claim."""
    while candidates:
        candidate = candidates.popleft()
        if try_claim_and_submit(
            worker,
            workspace,
            candidate.definition,
            candidate.job,
            candidate.node,
            candidate.job_dir,
            candidate.control_snapshot,
            candidate.allowed,
            snapshot,
            execution_generation=candidate.execution_generation,
        ):
            return True
    return False
