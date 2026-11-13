"""Ready-gated approval parking when execution capacity is unavailable."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.workflow_worker.scan import collect_ready_candidates
from server.app.workflows.approval_node import APPROVAL_NODE_TYPE
from server.app.workflows.definition import WorkflowDefinition

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread


def park_ready_approvals_without_capacity(worker: WorkflowWorkerThread) -> bool:
    """Evaluate only jobs with approval rows, then park their ready gates."""
    rows = worker.job_db.list_approval_park_candidates()
    if not rows:
        return False
    definitions = {
        workspace_id: definition
        for workspace_id, _workflow_key, definition in worker.state.scan_entries
    }
    grouped: dict[str, list[tuple[WorkflowDefinition | None, dict[str, Any]]]] = {}
    paused: dict[str, bool] = {}
    for row in rows:
        workspace_id = str(row["workspace_id"])
        if workspace_id not in paused:
            paused[workspace_id] = worker._is_paused(workspace_id)
        if paused[workspace_id]:
            worker.state.pass_skips["paused_jobs"] += 1
            continue
        grouped.setdefault(workspace_id, []).append((definitions.get(workspace_id), row))

    worker.state.last_ready_stats = {"hit": 0, "miss": 0}
    parked = False
    for workspace_id, jobs in grouped.items():
        candidates = collect_ready_candidates(worker, jobs)
        for candidate in candidates:
            if candidate.node.node_type != APPROVAL_NODE_TYPE:
                continue
            if worker._is_paused(workspace_id):
                break
            parked = (
                worker.leases.park_awaiting_approval(
                    candidate.job["id"],
                    candidate.node.key,
                    execution_generation=candidate.execution_generation,
                )
                or parked
            )
    return parked
