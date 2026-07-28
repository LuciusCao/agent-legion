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
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.executors.models import ConfigurationFailureRequest
from server.app.executors.scheduling.capacity import CapacitySnapshot
from server.app.services.node_config import (
    batch_source_payload,
    dispatch_effective_config,
    executor_definition_capability_schema,
)
from server.app.workflow_worker_executor_claim import claim_executor_node
from server.app.workflow_worker_routing import resolve_node_route
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


def _fail_node_config(
    worker: WorkflowWorkerThread,
    workspace_id: str,
    job: dict[str, Any],
    workflow_key: str,
    node: WorkflowNode,
    log_path: Path,
    message: str,
) -> bool:
    """Fail a node that can never run due to configuration, without a lease."""
    worker.leases.fail_without_lease(
        ConfigurationFailureRequest(
            workspace_id=workspace_id,
            job_id=job["id"],
            workflow_key=workflow_key,
            node_key=node.key,
            capability=node.capability,
            log_path=str(log_path),
        ),
        message,
    )
    return True


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
    inputs = tuple(node.inputs)
    if node.reduce is not None:
        assemble_reduce_inputs(worker, job["id"], node, job_dir)
        inputs = (*inputs, f"{node.key}.shards.json")
    log_path = worker.settings.logs_dir.resolve() / "jobs" / f"{job['id']}-{node_key}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    resolved = resolve_node_route(worker, workspace_id, workflow_key, node_key, node.capability)
    if resolved.kind == "error":
        return _fail_node_config(
            worker, workspace_id, job, workflow_key, node, log_path, resolved.error_message
        )
    if resolved.kind == "agent":
        agent_id = resolved.target_id
        definition_config = worker.settings.agent_definitions.get(agent_id)
        if definition_config is None:  # resolve_node_route already validated this
            return _fail_node_config(
                worker,
                workspace_id,
                job,
                workflow_key,
                node,
                log_path,
                f"Invalid Agent route {agent_id!r}",
            )
        if worker.agent_dispatch is None:
            raise RuntimeError("Agent dispatch service is not configured")
        try:
            node_config = dispatch_effective_config(
                definition_config.config_schema,
                node,
                workflow_key,
                workspace,
                batch_source_payload(worker.job_db, job),
            )
            return worker.agent_dispatch.enqueue(
                agent_id=agent_id,
                definition=definition_config,
                workspace=workspace,
                job=job,
                workflow_key=workflow_key,
                node=node,
                job_dir=job_dir,
                log_path=log_path,
                inputs=inputs,
                node_config=node_config,
            )
        except ValueError as exc:
            # Route/definition/capacity drift must fail THIS node, not
            # abort the whole poll pass and starve every workspace.
            return _fail_node_config(
                worker, workspace_id, job, workflow_key, node, log_path, str(exc)
            )

    executor_id = resolved.target_id

    try:
        node_config = dispatch_effective_config(
            executor_definition_capability_schema(
                worker.settings.executor_definitions, executor_id, node.capability
            ),
            node,
            workflow_key,
            workspace,
            batch_source_payload(worker.job_db, job),
        )
    except ValueError as exc:
        return _fail_node_config(worker, workspace_id, job, workflow_key, node, log_path, str(exc))

    return claim_executor_node(
        worker,
        workspace,
        job,
        node,
        job_dir,
        log_path,
        inputs,
        executor_id,
        resolved.local_node_limit,
        workflow_key,
        control_snapshot,
        allowed_node_keys,
        snapshot,
        node_config,
    )
