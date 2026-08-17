"""Agent-route claim submission for the workflow worker's ready candidates.

Split from ``schedule`` for size. Hosts the per-pass
batch-payload memoization shared by the agent and executor claim paths, and
the no-lease configuration-failure write used when a candidate can never run.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.executors.models import ConfigurationFailureRequest
from server.app.services.agent_version_pins import (
    agent_version_pin,
    resolve_dispatch_agent_definition,
)
from server.app.services.node_config import batch_source_payload, dispatch_effective_config
from server.app.skills.errors import SkillRepoError
from server.app.workflow_worker.agent_gate import agent_claim_allowed
from server.app.workflows.definition import WorkflowNode

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread


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
    if worker.agent_dispatch is None:
        raise RuntimeError("Agent dispatch service is not configured")
    dispatch = worker.agent_dispatch
    # Per-pass in-memory gates (batched active filter + stock limit); the
    # enqueue re-check on the pool thread stays authoritative. Gated
    # candidates must stay cheap: no batch-payload or definition reads.
    if not agent_claim_allowed(worker, str(workspace_id), str(job["id"]), node.key, agent_id):
        return False
    batch_payload = cached_batch_payload(worker, job)
    # Quality replay (schema v29): a frozen per-run Agent version pin in the
    # intake batch payload wins over the currently published definition.
    pin = agent_version_pin(batch_payload, node.key)
    try:
        definition_config = resolve_dispatch_agent_definition(
            worker.settings.database_url, str(workspace_id), agent_id, pin
        )
    except ValueError as exc:
        return fail_node_config(worker, workspace_id, job, workflow_key, node, log_path, str(exc))
    if definition_config is None:  # resolve_node_route already validated this
        return fail_node_config(
            worker,
            workspace_id,
            job,
            workflow_key,
            node,
            log_path,
            f"Agent {agent_id!r} has no published definition in workspace {workspace_id!r};"
            " agent definitions are workspace-scoped (schema v46) — create one in"
            " Studio (Agent 管理) for this workspace",
        )
    if pin is not None and definition_config.capability != node.capability:
        return fail_node_config(
            worker,
            workspace_id,
            job,
            workflow_key,
            node,
            log_path,
            f"pinned Agent version capability {definition_config.capability!r}"
            f" does not match node capability {node.capability!r}",
        )
    try:
        node_config = dispatch_effective_config(
            definition_config.config_schema,
            node,
            workflow_key,
            workspace,
            batch_payload,
        )
    except ValueError as exc:
        # Config drift must fail THIS node, not abort the whole poll pass.
        return fail_node_config(worker, workspace_id, job, workflow_key, node, log_path, str(exc))

    flight_key = (str(job["id"]), node.key)

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
                pinned_agent_version=int(pin["version"]) if pin is not None else None,
            )
        except (ValueError, SkillRepoError) as exc:
            # SkillRepoError (git clone/fetch/checkout 失败) 是 RuntimeError
            # 而非 ValueError，但必须同样按节点失败处理，不能漏到 enqueue
            # 线程池。有意的折衷：瞬时 git 故障会把节点直接置失败而不是下轮
            # 重试——热路径 git 探测几乎不瞬时失败，fetch 仅在 locked commit
            # 缺失时发生，受影响的 job 可由用户重跑。
            fail_node_config(worker, workspace_id, job, workflow_key, node, log_path, str(exc))
        finally:
            worker._agent_pass.in_flight.discard(flight_key)

    # Staging + bundling run off the poll thread. Register in-flight before
    # submit so duplicate candidates are skipped until the closure finishes.
    worker._agent_pass.in_flight.add(flight_key)
    if not dispatch.enqueue_pool.submit(_enqueue):
        # Pool backlog full: skip this pass's remaining agent candidates.
        worker._agent_pass.in_flight.discard(flight_key)
        worker._agent_pass.pool_full = True
        return False
    # Count the submission toward the stock gate: the snapshot stays frozen
    # until refresh (over-counts on enqueue failure — conservative, fine).
    enqueued = worker._agent_pass.stock_enqueued
    stock_key = (str(workspace_id), agent_id)
    enqueued[stock_key] = enqueued.get(stock_key, 0) + 1
    key = f"agent:{agent_id}"
    worker._pass_claim_counts[key] = worker._pass_claim_counts.get(key, 0) + 1
    return True
