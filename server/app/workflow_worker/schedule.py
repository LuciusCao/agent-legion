"""Lease claiming for the workflow worker's ready candidates.

Extracted from the worker thread to keep it within its size budget. The
capacity snapshot checks here are optimization hints that skip pointless
``try_claim`` write-lock acquisitions; the lease claim transaction remains the
authoritative capacity enforcement. Ready candidates are collected once per
poll pass by ``server.app.workflow_worker.ready``.
"""

from __future__ import annotations

import logging
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.executors.config import CodeExecutorConfig
from server.app.executors.scheduling.capacity import CapacitySnapshot
from server.app.services.job_errors import JobServiceError
from server.app.services.node_codes import resolve_dispatch_node_code
from server.app.services.vault import VaultError
from server.app.workflow_worker.agent_claim import (
    cached_batch_payload,
    claim_agent_node,
    fail_node_config,
)
from server.app.workflow_worker.code_claim import try_claim_code_worker_node
from server.app.workflow_worker.dispatch_config import resolve_dispatch_node_config
from server.app.workflow_worker.executor_claim import claim_executor_node
from server.app.workflow_worker.routing import resolve_node_route
from server.app.workflow_worker.shards import assemble_reduce_inputs, claim_shard_node
from server.app.workflows.definition import WorkflowDefinition, WorkflowNode

if TYPE_CHECKING:
    from server.app.workflow_worker.ready import ReadyCandidate
    from server.app.workflow_worker.thread import WorkflowWorkerThread

logger = logging.getLogger(__name__)


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
        for workspace_id in worker._round_robin.order(list(queues)):
            queue = queues.get(workspace_id)
            if queue is None or worker._is_paused(workspace_id):
                continue
            if claim_next_candidate(worker, workspaces[workspace_id], queue, snapshot):
                round_claimed = True
                claims += 1
                worker._round_robin.complete_pass(workspace_id)
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
    inputs = tuple(node.inputs)
    if node.reduce is not None:
        assemble_reduce_inputs(worker, job["id"], node, job_dir)
        inputs = (*inputs, f"{node.key}.shards.json")
    log_path = worker.settings.logs_dir.resolve() / "jobs" / f"{job['id']}-{node_key}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    resolved = resolve_node_route(worker, workspace_id, workflow_key, node_key, node.capability)
    if resolved.kind == "error":
        return fail_node_config(
            worker, workspace_id, job, workflow_key, node, log_path, resolved.error_message
        )
    if resolved.kind == "agent":
        # Once the enqueue pool filled up this pass, skip remaining agent
        # candidates outright (route came from the TTL cache: zero DB).
        if worker._agent_pass.pool_full is True:
            return False
        return claim_agent_node(
            worker,
            workspace,
            job,
            node,
            job_dir,
            log_path,
            inputs,
            resolved.target_id,
            workflow_key,
        )

    executor_id = resolved.target_id

    # Batch 2: a code-executor candidate with an online code-capable Worker
    # and a Worker-eligible payload is enqueued to the broker; anything else
    # falls through to the local executor path below (the safety net).
    if isinstance(
        worker.settings.executor_definitions.get(executor_id), CodeExecutorConfig
    ) and try_claim_code_worker_node(
        worker, workspace, job, node, job_dir, log_path, inputs, executor_id, workflow_key
    ):
        return True

    # Cheap gate before config resolution: when the pass snapshot says this
    # executor/workspace is out of capacity, the claim cannot succeed
    # (claim_executor_node re-checks authoritatively), so skip the per-pop
    # batch lookup and config resolution for the thousands of doomed
    # candidates that pile up behind a saturated executor.
    if worker.registry.global_capacity(executor_id) is None:
        return False
    if not snapshot.has_capacity(executor_id, workspace_id):
        return False

    try:
        batch_payload = cached_batch_payload(worker, job)
        # Frozen snapshot → vault secret_refs → connection config + token;
        # all in-memory only (VAULT-SECRET-001, CONFIG-MANIFEST-001).
        node_config = resolve_dispatch_node_config(
            worker, executor_id, node, workflow_key, workspace_id, workspace, batch_payload
        )
    except (ValueError, VaultError, JobServiceError) as exc:
        return fail_node_config(worker, workspace_id, job, workflow_key, node, log_path, str(exc))

    # Custom node code (EXEC-CODE-002): only code-kind executors can carry
    # custom code, so other kinds skip the DB read entirely. Frozen job
    # version wins over the current published version; None keeps builtin.
    # A frozen-pin hash mismatch fails the node (fail closed, EXEC-CODE-003).
    node_code = None
    if isinstance(worker.settings.executor_definitions.get(executor_id), CodeExecutorConfig):
        frozen_pins = (batch_payload or {}).get("node_code_versions") or {}
        try:
            node_code = resolve_dispatch_node_code(
                worker.job_db.path,
                worker.settings.executor_runtime.workflows.custom_nodes_enabled,
                workspace_id,
                workflow_key,
                node_key,
                frozen_pins.get(node_key),
            )
        except ValueError as exc:
            return fail_node_config(
                worker, workspace_id, job, workflow_key, node, log_path, str(exc)
            )

    claimed = claim_executor_node(
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
        node_code,
    )
    if claimed:
        worker._pass_claim_counts[executor_id] = worker._pass_claim_counts.get(executor_id, 0) + 1
    return claimed
