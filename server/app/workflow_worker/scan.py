"""Dirty-tracking scan helpers for the workflow worker.

A job's evaluation inputs change only when its ``jobs`` row changes
(claim/finish bump ``updated_at``; execution control and workflow hash are
columns), so each poll pass diffs lightweight job marks against the per-job
evaluation cache and only re-evaluates jobs whose marks changed. Jobs with
any running node are always re-evaluated: shard nodes can gain pending
shards without any row change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.workflow_worker.ready_cache import (
    ReadyCandidate,
    evaluate_job_ready,
    resolve_cached_definition,
)
from server.app.workflows.definition import WorkflowDefinition

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread


def mark_key(mark: dict[str, Any]) -> tuple[Any, ...]:
    """Everything besides node statuses that can change a job's evaluation."""
    return (
        mark.get("status"),
        bool(mark.get("execution_paused")),
        mark.get("execution_mode"),
        mark.get("target_node_key"),
        mark.get("workflow_definition_hash"),
        mark.get("updated_at"),
    )


def is_runnable(mark: dict[str, Any]) -> bool:
    return mark.get("status") not in ("completed", "failed", "paused") and not mark.get(
        "execution_paused"
    )


def collect_ready_candidates(
    worker: WorkflowWorkerThread,
    jobs: list[tuple[WorkflowDefinition, dict[str, Any]]],
) -> list[ReadyCandidate]:
    """Evaluate changed jobs, reuse cached candidates for unchanged ones.

    ``jobs`` carries lightweight marks (see
    ``JobScanMarksMixin.list_active_job_marks``); fat rows and node statuses
    are loaded only for jobs whose mark changed since the previous pass.
    Results are stored in ``worker._job_evals`` keyed by job id.
    """
    runnable = [(definition, mark) for definition, mark in jobs if is_runnable(mark)]
    changed: list[tuple[WorkflowDefinition, dict[str, Any]]] = []
    for definition, mark in runnable:
        cached = worker._job_evals.get(mark["id"])
        if cached is not None and cached[0] == mark_key(mark) and mark.get("status") != "running":
            worker._last_ready_stats["hit"] += 1
            continue
        worker._last_ready_stats["miss"] += 1
        changed.append((definition, mark))
    if changed:
        workspace_id = str(changed[0][1]["workspace_id"])
        fat_rows = {
            str(job["id"]): job
            for job in worker.job_db.list_jobs_by_ids(
                workspace_id, [mark["id"] for _, mark in changed]
            )
        }
        nodes_by_job = worker.job_db.list_job_nodes_for_jobs([mark["id"] for _, mark in changed])
        for definition, mark in changed:
            job = fat_rows.get(mark["id"])
            if job is None:
                continue  # deleted between the marks query and now
            definition_to_run = resolve_cached_definition(worker, definition, job)
            statuses = {
                node["node_key"]: node["status"] for node in nodes_by_job.get(job["id"], [])
            }
            evaluated = evaluate_job_ready(worker, definition_to_run, job, statuses)
            worker._job_evals[job["id"]] = (mark_key(mark), evaluated)
    candidates: list[ReadyCandidate] = []
    for _, mark in runnable:
        cached = worker._job_evals.get(mark["id"])
        if cached is not None:
            candidates.extend(cached[1])
    return candidates
