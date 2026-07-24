"""Ready-candidate collection for the workflow worker.

Each poll pass evaluates every runnable job at most once: this module walks a
workspace's jobs a single time, loads node statuses with one batched query,
and builds an ordered queue of ready nodes. The worker thread then pops one
candidate per scheduling round, which preserves the round-robin fairness
semantics (EXEC-FAIRNESS-001) without re-scanning jobs. Per-job evaluation
and the ready-evaluation caches live in
``server.app.workflow_worker_ready_cache``.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Any

from server.app.workflow_worker_ready_cache import (
    ReadyCandidate,  # noqa: F401  (re-exported for workflow_worker_schedule)
    evaluate_job_ready,
)
from server.app.workflows.definition import WorkflowDefinition

if TYPE_CHECKING:
    from server.app.workflow_worker_thread import WorkflowWorkerThread


def collect_ready_candidates(
    worker: WorkflowWorkerThread,
    jobs: list[tuple[WorkflowDefinition, dict[str, Any]]],
) -> list[ReadyCandidate]:
    """Evaluate each runnable job once and return its ready nodes in scan order."""
    runnable = [
        (definition, job)
        for definition, job in jobs
        if job.get("status") not in ("completed", "failed", "paused")
        and not job.get("execution_paused")
    ]
    if not runnable:
        worker._ready_cache.clear()
        return []
    nodes_by_job = worker.job_db.list_job_nodes_for_jobs([job["id"] for _, job in runnable])
    candidates: list[ReadyCandidate] = []
    for definition, job in runnable:
        statuses = {node["node_key"]: node["status"] for node in nodes_by_job.get(job["id"], [])}
        candidates.extend(evaluate_job_ready(worker, definition, job, statuses))
    return candidates


def build_ready_queues(
    worker: WorkflowWorkerThread,
    workspace_ids: list[str],
    jobs_by_workspace: dict[str, list[tuple[WorkflowDefinition, dict[str, Any]]]],
) -> tuple[dict[str, dict[str, Any]], dict[str, deque[ReadyCandidate]]]:
    """Build the workspace row and ready queue per workspace with candidates."""
    workspaces: dict[str, dict[str, Any]] = {}
    queues: dict[str, deque[ReadyCandidate]] = {}
    worker._last_ready_stats = {"hit": 0, "miss": 0}
    for workspace_id in workspace_ids:
        workspace = worker.job_db.get_workspace(workspace_id)
        if workspace is None:
            continue
        candidates = collect_ready_candidates(worker, jobs_by_workspace[workspace_id])
        if candidates:
            workspaces[workspace_id] = workspace
            queues[workspace_id] = deque(candidates)
    # Prune cache entries for jobs that left the runnable set of ANY
    # workspace (completed, failed, paused, deleted) so the cache cannot
    # grow unboundedly. Pruning must happen after every workspace has been
    # evaluated: each workspace only sees its own jobs.
    runnable_ids = {job["id"] for jobs in jobs_by_workspace.values() for _, job in jobs}
    for cached_id in list(worker._ready_cache):
        if cached_id not in runnable_ids:
            del worker._ready_cache[cached_id]
    return workspaces, queues
