"""Lease claim and context assembly for executor-routed workflow nodes.

Split from ``workflow_worker_schedule`` to keep that module within its size
budget; mirrors the shard claim path in ``workflow_worker_shards``.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.executors.models import ExecutionContext, LeaseClaimRequest
from server.app.executors.scheduling.capacity import CapacitySnapshot
from server.app.workflow_worker_execution import submit_claim
from server.app.workflows.definition import WorkflowNode

if TYPE_CHECKING:
    from server.app.workflow_worker_thread import WorkflowWorkerThread


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
    """Claim executor capacity and submit the execution; False on capacity loss."""
    workspace_id = workspace["id"]
    global_capacity = worker.registry.global_capacity(executor_id)
    if global_capacity is None:
        return False
    if not snapshot.has_capacity(executor_id, workspace_id):
        return False

    claim = worker.leases.try_claim(
        LeaseClaimRequest(
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
            target_node_key=control_snapshot.get("target_node_key") if control_snapshot else None,
            allowed_node_keys=tuple(sorted(allowed_node_keys)) if allowed_node_keys else (),
        )
    )
    if claim is None:
        return False
    snapshot.record_claim(executor_id, workspace_id)

    context = ExecutionContext(
        execution_id=claim.execution_id,
        lease_id=claim.lease_id,
        node_run_id=claim.node_run_id,
        executor_id=claim.executor_id,
        workspace_id=claim.workspace_id,
        job_id=claim.job_id,
        workflow_key=claim.workflow_key,
        node_key=claim.node_key,
        capability=claim.capability,
        workspace=dict(workspace),
        job=dict(job),
        job_dir=job_dir,
        log_path=log_path,
        inputs=inputs,
        expected_outputs=tuple(node.outputs),
        runtime={"node_execution": asdict(node.execution)},
        node_config=node_config,
    )

    submit_claim(worker, executor_id, claim, context)
    return True
