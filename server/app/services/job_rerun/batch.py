"""Set-based batch execution for the batch-rerun write paths.

Read/write split: one prefetch (narrow bulk job rows + node states + active
leases, joined in memory) resolves every job's target node and eligibility
with the exact per-job rules (``job_rerun.preview_checks``); only jobs that
pass go through the write portion (``commit_rerun``), which still re-guards
inside its DB transaction. Per-job results are identical to looping
``rerun()`` — same errors, same result dicts, same order.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.services.job_operation_error import JobOperationError, JobOperationResult
from server.app.services.job_rerun.by_failure_results import (
    assemble_rerun_targets,
    job_failure_result,
)
from server.app.services.job_rerun.eligibility import (
    AUTO_STRATEGIES,
    failed_nodes_by_job,
    resolve_failure_rerun_targets,
)
from server.app.services.job_rerun.preview_checks import (
    PreviewDefinitions,
    rerun_ineligible_from_nodes,
    resolve_rerun_node_from_nodes,
)
from server.app.services.job_rerun.single import commit_rerun_result
from server.app.services.job_selection_resolver import resolve_batch_selection
from server.app.workflows.workflow_branching import downstream_nodes

if TYPE_CHECKING:
    from server.app.services.job_rerun import JobRerunService


@dataclass
class PrefetchedRerunState:
    """One bulk fetch per data domain, shared by all jobs of a batch."""

    jobs: dict[str, dict[str, Any]]
    nodes_by_job: dict[str, list[dict[str, Any]]]
    busy_pairs: set[tuple[str, str]]
    definitions: PreviewDefinitions


def prefetch_rerun_state(service: JobRerunService, ids: Collection[str]) -> PrefetchedRerunState:
    ordered = list(ids)
    return PrefetchedRerunState(
        jobs=service.job_db.list_job_rerun_states_for_jobs("", ordered),
        nodes_by_job=service.job_db.list_job_node_states_for_jobs(ordered),
        busy_pairs=service.lease_repo.active_lease_node_keys_for_jobs(ordered, service._now()),
        definitions=PreviewDefinitions(service),
    )


def plan_rerun_target(
    pre: PrefetchedRerunState,
    workspace_id: str,
    job_id: str,
    node_key: str | None,
    from_failed_node: bool,
) -> tuple[str, str] | JobOperationError:
    """In-memory resolve + eligibility for one job: (job_id, node) or the error."""
    job = pre.jobs.get(job_id)
    if job is None:
        return JobOperationError(job_id, "rerun", "failed", node_key, "not_found", "Job not found")
    if job["workspace_id"] != workspace_id:
        return JobOperationError(
            job_id,
            "rerun",
            "failed",
            node_key,
            "wrong_workspace",
            f"Job does not belong to workspace {workspace_id}",
        )
    nodes = pre.nodes_by_job.get(job_id, [])
    try:
        actual_node_key = resolve_rerun_node_from_nodes(
            job_id, job, nodes, node_key, from_failed_node
        )
    except JobOperationError as exc:
        return exc
    ineligible = rerun_ineligible_from_nodes(
        pre.definitions.for_job(job), nodes, pre.busy_pairs, job_id, actual_node_key
    )
    if ineligible is not None:
        return ineligible
    return (job_id, actual_node_key)


def commit_planned_reruns(
    service: JobRerunService,
    pre: PrefetchedRerunState,
    workspace_id: str,
    plans: list[tuple[str, str]],
) -> dict[str, JobOperationResult]:
    """Write phase: full rows for eligible jobs in one query, one commit each."""
    full_jobs = {
        str(job["id"]): job
        for job in service.job_db.list_jobs_by_ids(workspace_id, [job_id for job_id, _ in plans])
    }
    outcomes: dict[str, JobOperationResult] = {}
    for job_id, actual_node_key in plans:
        outcomes[job_id] = commit_rerun_result(
            service,
            full_jobs[job_id],
            job_id,
            actual_node_key,
            definition=pre.definitions.for_job(pre.jobs[job_id]),
        )
    return outcomes


def batch_rerun(
    service: JobRerunService,
    workspace_id: str,
    job_ids: list[str] | None = None,
    node_key: str | None = None,
    *,
    from_failed_node: bool = False,
    job_filter: JobListFilter | None = None,
    exclude_ids: Collection[str] = (),
) -> list[JobOperationResult]:
    ids = list(
        dict.fromkeys(
            value.strip()
            for value in resolve_batch_selection(
                service.job_db, workspace_id, job_ids, job_filter, exclude_ids
            )
            if value.strip()
        )
    )
    if not ids:
        return []
    pre = prefetch_rerun_state(service, ids)
    plans: list[tuple[str, str]] = []
    results: dict[str, JobOperationResult] = {}
    for job_id in ids:
        outcome = plan_rerun_target(pre, workspace_id, job_id, node_key, from_failed_node)
        if isinstance(outcome, tuple):
            plans.append(outcome)
        else:
            results[job_id] = outcome.to_result()
    results.update(commit_planned_reruns(service, pre, workspace_id, plans))
    return [results[job_id] for job_id in ids]


def rerun_by_failure_category(
    service: JobRerunService,
    workspace_id: str,
    category: str,
    *,
    strategy: str = "auto",
    job_ids: list[str] | tuple[str, ...] = (),
    workflow_key: str | None = None,
    job_filter: JobListFilter | None = None,
    exclude_ids: Collection[str] = (),
    from_node_key: str | None = None,
) -> list[dict[str, Any]]:
    """Rerun the latest failed node runs of one category, one result per job.

    Same read/write split as ``batch_rerun``: the matching set and per-job
    targets are planned against one prefetch; commits run only for eligible
    targets. ``entries`` keep target order so ``rerun_nodes`` matches the
    per-job path exactly.
    """
    resolved = AUTO_STRATEGIES.get(category, "rerun_self") if strategy == "auto" else strategy
    ids = resolve_batch_selection(service.job_db, workspace_id, job_ids, job_filter, exclude_ids)
    requested = [value.strip() for value in ids if value.strip()]
    grouped = failed_nodes_by_job(service, workspace_id, category, requested, workflow_key)

    pre = prefetch_rerun_state(service, list(grouped))
    # Per target: either a ready error result or the target key (to commit).
    plans: list[tuple[str, list[JobOperationResult | str], Any]] = []
    results: dict[str, dict[str, Any]] = {}
    for job_id, failed_nodes in grouped.items():
        job = pre.jobs.get(job_id)
        if job is None:
            results[job_id] = job_failure_result(job_id, "failed", "not_found", "Job not found")
            continue
        if job["workspace_id"] != workspace_id:
            results[job_id] = job_failure_result(
                job_id,
                "failed",
                "wrong_workspace",
                f"Job does not belong to workspace {workspace_id}",
            )
            continue
        definition = pre.definitions.for_job(job)
        if from_node_key is not None:
            # Explicit start node: only jobs whose matching failure is the node
            # itself or downstream of it rerun, starting from from_node_key.
            downstream = set(downstream_nodes(definition, from_node_key))
            if not any(node == from_node_key or node in downstream for node in failed_nodes):
                results[job_id] = job_failure_result(
                    job_id,
                    "skipped",
                    "no_matching_failure",
                    f"No latest failed run at or downstream of node {from_node_key!r}",
                )
                continue
            targets = [from_node_key]
        else:
            targets = resolve_failure_rerun_targets(definition, failed_nodes, resolved)
        nodes = pre.nodes_by_job.get(job_id, [])
        entries: list[JobOperationResult | str] = []
        for target in targets:
            ineligible = rerun_ineligible_from_nodes(
                definition, nodes, pre.busy_pairs, job_id, target
            )
            entries.append(ineligible.to_result() if ineligible is not None else target)
        plans.append((job_id, entries, definition))

    if plans:
        full_jobs = {
            str(job["id"]): job
            for job in service.job_db.list_jobs_by_ids(
                workspace_id, [job_id for job_id, _, _ in plans]
            )
        }
        for job_id, entries, definition in plans:
            node_results = [
                commit_rerun_result(
                    service, full_jobs[job_id], job_id, entry, definition=definition
                )
                if isinstance(entry, str)
                else entry
                for entry in entries
            ]
            results[job_id] = assemble_rerun_targets(job_id, node_results)

    ordered = [results[job_id] for job_id in grouped]
    for job_id in requested:
        if job_id not in grouped:
            ordered.append(
                job_failure_result(
                    job_id,
                    "skipped",
                    "no_matching_failure",
                    f"No latest failed run with category {category!r}",
                )
            )
    return ordered
