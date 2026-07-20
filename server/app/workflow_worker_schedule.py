"""Lease claiming for the workflow worker's ready candidates.

Extracted from the worker thread to keep it within its size budget. The
capacity snapshot checks here are optimization hints that skip pointless
``try_claim`` write-lock acquisitions; the lease claim transaction remains the
authoritative capacity enforcement. Ready candidates are collected once per
poll pass by ``server.app.workflow_worker_ready``.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.executors.models import (
    ConfigurationFailureRequest,
    ExecutionContext,
    LeaseClaimRequest,
)
from server.app.executors.scheduling.capacity import CapacitySnapshot
from server.app.jobs.queries.workspace_node_bindings import (
    get_binding,
    get_local_node_limit,
    has_local_node_limit,
)
from server.app.workflow_worker_shards import assemble_reduce_inputs, claim_shard_node
from server.app.workflows.definition import WorkflowDefinition, WorkflowNode

if TYPE_CHECKING:
    from server.app.workflow_worker_ready import ReadyCandidate
    from server.app.workflow_worker_thread import WorkflowWorkerThread

logger = logging.getLogger(__name__)


def claim_next_candidate(
    worker: WorkflowWorkerThread,
    workspace: dict[str, Any],
    candidates: deque[ReadyCandidate],
    snapshot: CapacitySnapshot,
) -> bool:
    """Pop candidates until one claim is submitted; return True on a claim.

    Candidates whose claim fails (capacity lost to a race, stale execution
    target, ...) are dropped for this pass; the next poll pass re-evaluates
    them from fresh state.
    """
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
        ):
            return True
    return False


def try_claim_and_submit(
    worker: WorkflowWorkerThread,
    workspace: dict[str, Any],
    definition: WorkflowDefinition,
    job: dict[str, Any],
    node: WorkflowNode,
    job_dir: Path,
    control_snapshot: dict[str, Any] | None,
    allowed_node_keys: frozenset[str] | None,
    snapshot: CapacitySnapshot,
) -> bool:
    workspace_id = workspace["id"]
    workflow_key = definition.key
    node_key = node.key
    if node.shard is not None:
        return claim_shard_node(
            worker, workspace, job, node, job_dir, control_snapshot, allowed_node_keys, snapshot
        )
    if node.reduce is not None:
        assemble_reduce_inputs(worker, job["id"], node, job_dir)
    log_path = worker.settings.logs_dir.resolve() / "jobs" / f"{job['id']}-{node_key}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with worker.job_db._connect_read() as conn:
        binding = get_binding(conn, workspace_id, workflow_key, node_key)
        if binding is None:
            worker.leases.fail_without_lease(
                ConfigurationFailureRequest(
                    workspace_id=workspace_id,
                    job_id=job["id"],
                    workflow_key=workflow_key,
                    node_key=node_key,
                    capability=node.capability,
                    log_path=str(log_path),
                ),
                "No Executor binding",
            )
            return True

        executor_id = binding["executor_id"]
        try:
            executor = worker.registry.require(executor_id, node.capability)
        except Exception as exc:
            worker.leases.fail_without_lease(
                ConfigurationFailureRequest(
                    workspace_id=workspace_id,
                    job_id=job["id"],
                    workflow_key=workflow_key,
                    node_key=node_key,
                    capability=node.capability,
                    log_path=str(log_path),
                ),
                str(exc),
            )
            return True

        local_node_limit: int | None = None
        if executor.kind == "local":
            local_node_limit = get_local_node_limit(conn, workspace_id, workflow_key, node_key)
        elif has_local_node_limit(conn, workspace_id, workflow_key, node_key):
            worker.leases.fail_without_lease(
                ConfigurationFailureRequest(
                    workspace_id=workspace_id,
                    job_id=job["id"],
                    workflow_key=workflow_key,
                    node_key=node_key,
                    capability=node.capability,
                    log_path=str(log_path),
                ),
                "Node limits are not supported for agent executors",
            )
            return True

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
            node_key=node_key,
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
        inputs=tuple(node.inputs),
        expected_outputs=tuple(node.outputs),
        runtime={"node_execution": asdict(node.execution)},
    )

    pool = worker._pool_for(executor_id)
    future = pool.submit(worker._run_claim, claim, context)
    worker._futures[claim.execution_id] = future
    return True
