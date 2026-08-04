"""Lease claim preparation for executor-routed workflow nodes.

Split from ``schedule`` to keep it within its size budget.
Claims are buffered as ``PreparedClaim`` and leased in one batch transaction
at pass end (``server.app.workflow_worker.claim_flush``); the capacity
snapshot checks here are optimization hints that skip pointless preparation,
the lease claim transaction remains the authoritative capacity enforcement.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.executors.models import LeaseClaimRequest
from server.app.executors.scheduling.capacity import CapacitySnapshot
from server.app.workflow_worker.claim_flush import PreparedClaim
from server.app.workflows.definition import WorkflowNode

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread


def claim_executor_node(
    worker: WorkflowWorkerThread,
    workspace: dict[str, Any],
    job: dict[str, Any],
    node: WorkflowNode,
    job_dir: Path,
    log_path: Path,
    inputs: tuple[str, ...],
    executor_id: str,
    local_node_limit: int | None,
    workflow_key: str,
    control_snapshot: dict[str, Any] | None,
    allowed_node_keys: frozenset[str] | None,
    snapshot: CapacitySnapshot,
    node_config: dict[str, Any],
) -> bool:
    """Buffer an executor claim for the pass-end batch lease; False on capacity loss."""
    workspace_id = workspace["id"]
    global_capacity = worker.registry.global_capacity(executor_id)
    if global_capacity is None:
        return False
    if not snapshot.has_capacity(executor_id, workspace_id):
        return False

    worker._pending_claims.append(
        PreparedClaim(
            request=LeaseClaimRequest(
                executor_id=executor_id,
                global_capacity=global_capacity,
                workspace_id=workspace_id,
                job_id=job["id"],
                workflow_key=workflow_key,
                node_key=node.key,
                capability=node.capability,
                local_node_limit=local_node_limit,
                lease_ttl_seconds=worker.runtime.lease_ttl_seconds,
                log_path=str(log_path),
                execution_mode=control_snapshot.get("execution_mode", "full")
                if control_snapshot
                else "full",
                target_node_key=control_snapshot.get("target_node_key")
                if control_snapshot
                else None,
                allowed_node_keys=tuple(sorted(allowed_node_keys)) if allowed_node_keys else (),
            ),
            executor_id=executor_id,
            workspace=workspace,
            job=job,
            node=node,
            job_dir=job_dir,
            log_path=log_path,
            inputs=inputs,
            node_config=node_config,
        )
    )
    snapshot.record_claim(executor_id, workspace_id)
    return True
