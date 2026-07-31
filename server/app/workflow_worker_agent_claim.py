"""Agent-route claim submission for the workflow worker's ready candidates.

Split from ``workflow_worker_schedule`` for size. Hosts the per-pass
batch-payload memoization shared by the agent and executor claim paths, and
the no-lease configuration-failure write used when a candidate can never run.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.executors.models import ConfigurationFailureRequest
from server.app.services.node_config import batch_source_payload, dispatch_effective_config
from server.app.workflow_worker_agent_gate import agent_claim_allowed
from server.app.workflows.definition import WorkflowNode

if TYPE_CHECKING:
    from server.app.workflow_worker_thread import WorkflowWorkerThread


def cached_batch_payload(
    worker: WorkflowWorkerThread, job: dict[str, Any]
) -> dict[str, Any] | None:
    """Per-pass memoized ``batch_source_payload``: jobs share a handful of
    intake batches, so one lookup per batch per pass replaces one per candidate."""
    batch_id = job.get("batch_id")
    if not batch_id:
        return None
    cache = worker._batch_payload_cache
    key = str(batch_id)
    if key not in cache:
        cache[key] = batch_source_payload(worker.job_db, job)
    return cache[key]


def fail_node_config(
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


def claim_agent_node(
    worker: WorkflowWorkerThread,
    workspace: dict[str, Any],
    job: dict[str, Any],
    node: WorkflowNode,
    job_dir: Path,
    log_path: Path,
    inputs: tuple[str, ...],
    agent_id: str,
    workflow_key: str,
) -> bool:
    """Enqueue an agent-routed candidate; False when it already has a request."""
    workspace_id = workspace["id"]
    definition_config = worker.settings.agent_definitions.get(agent_id)
    if definition_config is None:  # resolve_node_route already validated this
        return fail_node_config(
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
    dispatch = worker.agent_dispatch
    # Per-pass in-memory gates (batched active filter + stock limit); the
    # enqueue re-check on the pool thread stays authoritative.
    if not agent_claim_allowed(worker, str(workspace_id), str(job["id"]), node.key, agent_id):
        return False
    try:
        node_config = dispatch_effective_config(
            definition_config.config_schema,
            node,
            workflow_key,
            workspace,
            cached_batch_payload(worker, job),
        )
    except ValueError as exc:
        # Route/definition/capacity drift must fail THIS node, not
        # abort the whole poll pass and starve every workspace.
        return fail_node_config(worker, workspace_id, job, workflow_key, node, log_path, str(exc))

    def _enqueue() -> None:
        try:
            dispatch.enqueue(
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
            fail_node_config(worker, workspace_id, job, workflow_key, node, log_path, str(exc))

    # Staging + bundling run off the poll thread; the broker's one-active-
    # request index dedups a resubmission if the next pass arrives first.
    if not dispatch.enqueue_pool.submit(_enqueue):
        # Pool backlog full: skip this pass's remaining agent candidates.
        worker._agent_pass.pool_full = True
        return False
    key = f"agent:{agent_id}"
    worker._pass_claim_counts[key] = worker._pass_claim_counts.get(key, 0) + 1
    return True
