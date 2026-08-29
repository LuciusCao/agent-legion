"""Batch evaluation for changed workflow-worker jobs.

Extracted from ``scan`` so that module stays within its size budget. One pass
precomputes branch evaluations, issues one batched ``not_applicable`` write and
one batched shard-pending read, then finishes per-job evaluation without any
per-job database round trips.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from server.app.storage_paths import resolve_job_dir
from server.app.workflow_worker.ready_cache import evaluate_job_ready, resolve_cached_definition
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.sharding_batch import has_pending_shards_many
from server.app.workflows.workflow_branching import RUNNABLE_STATUSES, evaluate_branches

if TYPE_CHECKING:
    from server.app.workflow_worker.ready_cache import ReadyCandidate
    from server.app.workflow_worker.thread import WorkflowWorkerThread

logger = logging.getLogger(__name__)


def evaluate_changed_jobs(
    worker: WorkflowWorkerThread,
    changed: list[tuple[WorkflowDefinition | None, dict[str, Any]]],
    fat_rows: dict[str, dict[str, Any]],
    nodes_by_job: dict[str, list[dict[str, Any]]],
    mark_key: Any,
) -> dict[str, tuple[Any, list[ReadyCandidate]]]:
    """Evaluate a batch of changed jobs with only two DB round trips.

    Returns a mapping from job id to the cached evaluation tuple that the
    caller stores in ``worker.state.job_evals``.
    """
    eval_contexts: list[dict[str, Any]] = []
    not_applicable_entries: list[tuple[str, list[str], str]] = []
    shard_node_pairs: list[tuple[str, str]] = []

    for definition, mark in changed:
        job = fat_rows.get(mark["id"])
        if job is None:
            continue
        definition_to_run = resolve_cached_definition(worker, definition, job)
        if definition_to_run is None:
            # Registered workflow without a catalog definition and a job
            # without a snapshot: nothing to evaluate against.
            logger.warning("skipping job %s: no workflow definition available", job["id"])
            continue
        statuses = {node["node_key"]: node["status"] for node in nodes_by_job.get(job["id"], [])}
        job_dir = resolve_job_dir(job, worker.settings.jobs_dir)
        branch_evaluation = evaluate_branches(definition_to_run, statuses, job_dir)
        for key in branch_evaluation.not_applicable:
            if statuses.get(key) in RUNNABLE_STATUSES:
                statuses[key] = "not_applicable"
        eval_contexts.append(
            {
                "mark": mark,
                "job": job,
                "definition": definition_to_run,
                "statuses": statuses,
                "branch_not_applicable": branch_evaluation.not_applicable,
            }
        )
        if branch_evaluation.not_applicable:
            not_applicable_entries.append(
                (job["id"], sorted(branch_evaluation.not_applicable), "unselected workflow branch")
            )
        for node in definition_to_run.nodes.values():
            if node.shard is not None and statuses.get(node.key) == "running":
                shard_node_pairs.append((job["id"], node.key))

    worker.job_db.mark_nodes_not_applicable_many(not_applicable_entries)
    with worker.job_db._connect_read() as conn:
        pending_shard_pairs = has_pending_shards_many(conn, shard_node_pairs)

    results: dict[str, tuple[Any, list[ReadyCandidate]]] = {}
    for ctx in eval_contexts:
        job_id = ctx["job"]["id"]
        pending_for_job = {
            node_key
            for (candidate_job_id, node_key) in pending_shard_pairs
            if candidate_job_id == job_id
        }
        evaluated = evaluate_job_ready(
            worker,
            ctx["definition"],
            ctx["job"],
            ctx["statuses"],
            branch_not_applicable=ctx["branch_not_applicable"],
            pending_shard_nodes=pending_for_job,
        )
        results[job_id] = (mark_key(ctx["mark"]), evaluated)
    return results
