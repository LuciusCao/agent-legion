"""Batch rerun of failed nodes selected by failure category."""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING, Any

from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.services._job_rerun_by_failure_results import (
    execute_rerun_targets,
    job_failure_result,
)
from server.app.services._job_rerun_eligibility import (
    failed_nodes_by_job,
    resolve_failure_rerun_targets,
)
from server.app.services.job_selection_resolver import resolve_batch_selection
from server.app.services.workflow_revision_format import definition_from_job_snapshot
from server.app.workflows.workflow_branching import downstream_nodes

if TYPE_CHECKING:
    from server.app.services.job_rerun import JobRerunService

# Hard-coded stage-1/2 defaults for strategy="auto"; workflow-declared
# policies arrive with stage 3.
_AUTO_STRATEGIES = {
    "technical": "rerun_self",
    "business": "rerun_upstream",
    "unknown": "rerun_self",
}


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
    """Rerun the latest failed node runs of one category, one result per job."""
    resolved = _AUTO_STRATEGIES.get(category, "rerun_self") if strategy == "auto" else strategy
    ids = resolve_batch_selection(service.job_db, workspace_id, job_ids, job_filter, exclude_ids)
    requested = [value.strip() for value in ids if value.strip()]
    grouped = failed_nodes_by_job(service, workspace_id, category, requested, workflow_key)

    results = [
        _rerun_job_failures(service, workspace_id, job_id, nodes, resolved, from_node_key)
        for job_id, nodes in grouped.items()
    ]
    for job_id in requested:
        if job_id not in grouped:
            results.append(
                job_failure_result(
                    job_id,
                    "skipped",
                    "no_matching_failure",
                    f"No latest failed run with category {category!r}",
                )
            )
    return results


def _rerun_job_failures(
    service: JobRerunService,
    workspace_id: str,
    job_id: str,
    failed_nodes: list[str],
    strategy: str,
    from_node_key: str | None = None,
) -> dict[str, Any]:
    job = service.job_db.get_job(job_id)
    if job is None:
        return job_failure_result(job_id, "failed", "not_found", "Job not found")
    if job["workspace_id"] != workspace_id:
        return job_failure_result(
            job_id, "failed", "wrong_workspace", f"Job does not belong to workspace {workspace_id}"
        )

    definition = definition_from_job_snapshot(job) or service.workflows.definition(
        str(job["workflow_key"])
    )
    if from_node_key is not None:
        # Explicit start node: only jobs whose matching failure is the node
        # itself or downstream of it rerun, starting from from_node_key.
        downstream = set(downstream_nodes(definition, from_node_key))
        if not any(node == from_node_key or node in downstream for node in failed_nodes):
            return job_failure_result(
                job_id,
                "skipped",
                "no_matching_failure",
                f"No latest failed run at or downstream of node {from_node_key!r}",
            )
        return execute_rerun_targets(service, job, job_id, [from_node_key])

    targets = resolve_failure_rerun_targets(definition, failed_nodes, strategy)
    return execute_rerun_targets(service, job, job_id, targets)
