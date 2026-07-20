"""Ready-candidate collection for the workflow worker.

Each poll pass evaluates every runnable job at most once: this module walks a
workspace's jobs a single time, loads node statuses with one batched query,
and builds an ordered queue of ready nodes. The worker thread then pops one
candidate per scheduling round, which preserves the round-robin fairness
semantics (EXEC-FAIRNESS-001) without re-scanning jobs.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.services.workflow_revision_format import definition_from_job_snapshot
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.definition import WorkflowDefinition, WorkflowNode
from server.app.workflows.execution_control import allowed_nodes
from server.app.workflows.scheduler import evaluate_branches, find_ready_nodes
from server.app.workflows.sharding import has_pending_shards
from server.app.workflows.workflow_branching import RUNNABLE_STATUSES

if TYPE_CHECKING:
    from server.app.workflow_worker_thread import WorkflowWorkerThread

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReadyCandidate:
    """One ready (job, node) pair plus everything ``try_claim_and_submit`` needs."""

    definition: WorkflowDefinition
    job: dict[str, Any]
    node: WorkflowNode
    job_dir: Path
    control_snapshot: dict[str, Any]
    allowed: frozenset[str]


def collect_ready_candidates(
    worker: WorkflowWorkerThread,
    jobs: list[tuple[WorkflowDefinition, dict[str, Any]]],
) -> list[ReadyCandidate]:
    """Evaluate each runnable job once and return its ready nodes in scan order.

    Semantics match the previous per-round evaluation: same job skipping, same
    branch evaluation, same ``allowed_nodes`` filtering and ``find_ready_nodes``
    ordering. ``mark_nodes_not_applicable`` still runs per job; its effect is
    mirrored into the in-memory statuses instead of re-querying them.
    """
    runnable = [
        (definition, job)
        for definition, job in jobs
        if job.get("status") not in ("completed", "failed", "paused")
        and not job.get("execution_paused")
    ]
    if not runnable:
        return []
    nodes_by_job = worker.job_db.list_job_nodes_for_jobs([job["id"] for _, job in runnable])
    candidates: list[ReadyCandidate] = []
    for definition, job in runnable:
        snapshot_definition = definition_from_job_snapshot(job)
        definition_to_run = snapshot_definition or definition
        job_dir = resolve_job_dir(job, worker.settings.jobs_dir)
        statuses = {node["node_key"]: node["status"] for node in nodes_by_job.get(job["id"], [])}
        branch_evaluation = evaluate_branches(definition_to_run, statuses, job_dir)
        worker.job_db.mark_nodes_not_applicable(
            job["id"],
            sorted(branch_evaluation.not_applicable),
            "unselected workflow branch",
        )
        # Mirror the update in memory instead of re-querying statuses:
        # mark_nodes_not_applicable only rewrites rows whose status is still
        # runnable, so only those keys flip to not_applicable.
        for key in branch_evaluation.not_applicable:
            if statuses.get(key) in RUNNABLE_STATUSES:
                statuses[key] = "not_applicable"
        control_snapshot = {
            "execution_mode": job.get("execution_mode", "full"),
            "target_node_key": job.get("target_node_key"),
            "execution_paused": bool(job.get("execution_paused")),
            "pause_reason": job.get("pause_reason", ""),
        }
        try:
            allowed = allowed_nodes(definition_to_run, control_snapshot)
        except Exception:
            logger.exception("failed to compute allowed nodes for job %s", job["id"])
            continue
        # A shard node whose aggregate row sits in 'running' may still hold
        # pending shards; mirror it as runnable so the rest of them dispatch.
        for node in definition_to_run.nodes.values():
            if node.shard is not None and statuses.get(node.key) == "running":
                with worker.job_db._connect_read() as conn:
                    if has_pending_shards(conn, job["id"], node.key):
                        statuses[node.key] = "pending"
        for node in find_ready_nodes(definition_to_run, statuses, job_dir):
            if node.key in allowed:
                candidates.append(
                    ReadyCandidate(
                        definition=definition_to_run,
                        job=job,
                        node=node,
                        job_dir=job_dir,
                        control_snapshot=control_snapshot,
                        allowed=allowed,
                    )
                )
    return candidates


def build_ready_queues(
    worker: WorkflowWorkerThread,
    workspace_ids: list[str],
    jobs_by_workspace: dict[str, list[tuple[WorkflowDefinition, dict[str, Any]]]],
) -> tuple[dict[str, dict[str, Any]], dict[str, deque[ReadyCandidate]]]:
    """Build the workspace row and ready queue per workspace with candidates."""
    workspaces: dict[str, dict[str, Any]] = {}
    queues: dict[str, deque[ReadyCandidate]] = {}
    for workspace_id in workspace_ids:
        workspace = worker.job_db.get_workspace(workspace_id)
        if workspace is None:
            continue
        candidates = collect_ready_candidates(worker, jobs_by_workspace[workspace_id])
        if candidates:
            workspaces[workspace_id] = workspace
            queues[workspace_id] = deque(candidates)
    return workspaces, queues
