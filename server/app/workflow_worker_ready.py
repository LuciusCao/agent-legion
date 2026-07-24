"""Ready-queue assembly for the workflow worker.

Per-pass scan and per-job evaluation live in
``server.app.workflow_worker_scan`` / ``server.app.workflow_worker_ready_cache``;
this module only groups each workspace's ready candidates into the queues the
claim loop drains, and prunes cached evaluations of jobs that left the
runnable set.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Any

from server.app.workflow_worker_ready_cache import (
    ReadyCandidate,  # noqa: F401  (re-exported for workflow_worker_schedule)
)
from server.app.workflow_worker_scan import collect_ready_candidates, is_runnable
from server.app.workflows.definition import WorkflowDefinition

if TYPE_CHECKING:
    from server.app.workflow_worker_thread import WorkflowWorkerThread


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
    # Prune evaluations for jobs that left the runnable set of ANY workspace
    # (completed, failed, paused, deleted) so the cache cannot grow
    # unboundedly. Pruning must happen after every workspace has been
    # evaluated: each workspace only sees its own jobs.
    runnable_ids = {
        mark["id"] for jobs in jobs_by_workspace.values() for _, mark in jobs if is_runnable(mark)
    }
    for cached_id in list(worker._job_evals):
        if cached_id not in runnable_ids:
            del worker._job_evals[cached_id]
    return workspaces, queues
