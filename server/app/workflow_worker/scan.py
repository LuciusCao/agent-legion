"""Dirty-tracking scan helpers for the workflow worker.

A job's evaluation inputs change only when its ``jobs`` row changes
(claim/finish bump ``updated_at``; execution control and workflow hash are
columns), so each poll pass diffs lightweight job marks against the per-job
evaluation cache and only re-evaluates jobs whose marks changed. Jobs with
any running node are always re-evaluated: shard nodes can gain pending
shards without any row change.
"""

from __future__ import annotations

from time import monotonic
from typing import TYPE_CHECKING, Any

from server.app.workflow_worker.eval_batch import evaluate_changed_jobs
from server.app.workflow_worker.ready_cache import ReadyCandidate
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
    jobs: list[tuple[WorkflowDefinition | None, dict[str, Any]]],
) -> list[ReadyCandidate]:
    """Evaluate changed jobs, reuse cached candidates for unchanged ones.

    ``jobs`` carries lightweight marks (see
    ``JobScanMarksMixin.list_active_job_marks``); fat rows and node statuses
    are loaded only for jobs whose mark changed since the previous pass.
    Results are stored in ``worker._job_evals`` keyed by job id.
    """
    runnable = [(definition, mark) for definition, mark in jobs if is_runnable(mark)]
    changed: list[tuple[WorkflowDefinition | None, dict[str, Any]]] = []
    for definition, mark in runnable:
        cached = worker._job_evals.get(mark["id"])
        if cached is not None and cached[0] == mark_key(mark) and mark.get("status") != "running":
            worker._last_ready_stats["hit"] += 1
            continue
        worker._last_ready_stats["miss"] += 1
        changed.append((definition, mark))
    if changed:
        workspace_id = str(changed[0][1]["workspace_id"])
        fetch_started = monotonic()
        fat_rows = {
            str(job["id"]): job
            for job in worker.job_db.list_jobs_by_ids(
                workspace_id, [mark["id"] for _, mark in changed]
            )
        }
        nodes_by_job = worker.job_db.list_job_nodes_for_jobs([mark["id"] for _, mark in changed])
        phases = worker._scan_phases
        phases["miss_fetch"] = phases.get("miss_fetch", 0.0) + monotonic() - fetch_started
        eval_started = monotonic()
        worker._job_evals.update(
            evaluate_changed_jobs(worker, changed, fat_rows, nodes_by_job, mark_key)
        )
        phases["eval"] = phases.get("eval", 0.0) + monotonic() - eval_started
    candidates: list[ReadyCandidate] = []
    for _, mark in runnable:
        cached = worker._job_evals.get(mark["id"])
        if cached is not None:
            candidates.extend(cached[1])
    return candidates
