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
    node_code: str | None = None,
    config_snapshot_json: str = "",
) -> bool:
    """Buffer an executor claim for the pass-end batch lease; False on capacity loss."""
    workspace_id = workspace["id"]
    # Single implicit code pool (P-0.5): the capacity comes from the instance
    # settings code_capacity, never from a caller-chosen executor definition.
    global_capacity = worker.settings.executor_runtime.code_capacity
    if not snapshot.has_capacity(workspace_id, workflow_key, node.key):
        return False

    worker.state.pending_claims.append(
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
                config_snapshot_json=config_snapshot_json,
            ),
            executor_id=executor_id,
            workspace=workspace,
            job=job,
            node=node,
            job_dir=job_dir,
            log_path=log_path,
            inputs=inputs,
            node_config=node_config,
            node_code=node_code,
        )
    )
    snapshot.record_claim(workspace_id, workflow_key, node.key)
    return True
