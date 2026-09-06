"""Local-pool shard claim execution (#389 split from ``shards.py``).

The shard claim loop lives in ``shards.py``; the local fallback half moved
here when the remote lane (#389) made that file outgrow its size budget.
Pure-remote mode (``code_capacity == 0``) never reaches this module — the
caller gates on the same config.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.executors.models import (
    CODE_EXECUTOR_ID,
    ExecutionContext,
    LeaseClaimRequest,
)
from server.app.executors.scheduling.capacity import CapacitySnapshot
from server.app.workflow_worker.execution import submit_claim
from server.app.workflows.definition import WorkflowNode

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread


def claim_shard_locally(
    worker: WorkflowWorkerThread,
    workspace: dict[str, Any],
    job: dict[str, Any],
    node: WorkflowNode,
    job_dir: Path,
    log_path: Path,
    *,
    shard_index: int,
    shard_input: Any,
    local_node_limit: int | None,
    control_snapshot: dict[str, Any] | None,
    allowed_node_keys: frozenset[str] | None,
    snapshot: CapacitySnapshot,
) -> bool:
    """Lease and submit one shard on the local code pool; False = no capacity."""
    workspace_id = workspace["id"]
    if not snapshot.has_capacity(workspace_id, node.key):
        return False
    claim = worker.leases.try_claim(
        LeaseClaimRequest(
            executor_id=CODE_EXECUTOR_ID,
            global_capacity=worker.settings.executor_runtime.code_capacity,
            workspace_id=workspace_id,
            job_id=job["id"],
            workflow_key=str(job["workspace_id"]),
            node_key=node.key,
            capability=node.capability,
            local_node_limit=local_node_limit,
            lease_ttl_seconds=worker.settings.executor_runtime.lease_ttl_seconds,
            log_path=str(log_path),
            execution_mode=control_snapshot.get("execution_mode", "full")
            if control_snapshot
            else "full",
            target_node_key=control_snapshot.get("target_node_key") if control_snapshot else None,
            allowed_node_keys=tuple(sorted(allowed_node_keys)) if allowed_node_keys else (),
            shard_index=shard_index,
        )
    )
    if claim is None:
        return False  # capacity lost to a race; the next poll pass re-evaluates
    snapshot.record_claim(workspace_id, node.key)
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
        inputs=tuple(node.inputs),
        # Mirror the remote contract (#389 review P2-2): the shard output
        # file is an expected output on both paths, so a shard node whose
        # code writes no shard payload fails identically locally and
        # remotely instead of silently yielding an empty reduce payload.
        # #401 review P1-2 (both paths): the node's ordinary outputs are
        # EXCLUDED — a shard's product contract is only the per-index file.
        # Concurrent shards share one job dir and one object-storage
        # authority key per (job, node, name); with many shards of a node
        # running at once (v79), sibling shards writing the same ordinary
        # output name would clobber each other's artifacts and races the
        # per-shard missing-output check.
        expected_outputs=(f"shard_output-{shard_index}.json",),
        runtime={
            "node_execution": asdict(node.execution),
            "shard_index": shard_index,
            "shard_input": shard_input,
        },
    )
    submit_claim(worker, CODE_EXECUTOR_ID, claim, context)
    return True
