"""Read-only batch-rerun preview using the execution target planners.

Selections use bulk job/node/lease reads joined in memory; the per-job predicates
are the same pure checks as execution, so planned targets cannot drift from it.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING, Any

from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.services.job_operation_error import JobOperationError
from server.app.services.job_rerun.eligibility import failed_nodes_by_job
from server.app.services.job_rerun.failure_plan import plan_failure_rerun
from server.app.services.job_rerun.preview_checks import (
    PreviewDefinitions,
    rerun_ineligible_from_nodes,
    resolve_rerun_node_from_nodes,
)
from server.app.services.job_selection_resolver import resolve_batch_selection

if TYPE_CHECKING:
    from server.app.services.job_rerun import JobRerunService


def batch_rerun_preview(
    service: JobRerunService,
    workspace_id: str,
    job_ids: list[str] | None = None,
    node_key: str | None = None,
    *,
    from_failed_node: bool = False,
    failure_category: str | None = None,
    strategy: str = "auto",
    from_node_key: str | None = None,
    job_filter: JobListFilter | None = None,
    exclude_ids: Collection[str] = (),
) -> dict[str, Any]:
    """Return counts and eligible planned targets for every selected job."""
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
        return {"total_count": 0, "eligible_count": 0, "plans": []}

    jobs = service.job_db.list_job_rerun_states_for_jobs(workspace_id, ids)
    nodes_by_job = service.job_db.list_job_node_states_for_jobs(ids)
    busy_pairs = service.lease_repo.active_lease_node_keys_for_jobs(ids, service._now())
    definitions = PreviewDefinitions(service)

    if failure_category is not None:
        planned = _failure_category_plans(
            service,
            definitions,
            jobs,
            nodes_by_job,
            busy_pairs,
            workspace_id,
            failure_category,
            strategy,
            from_node_key,
            ids,
        )
    else:
        planned = {}
        for job_id in ids:
            job = jobs.get(job_id)
            targets: list[str] = []
            if job is not None and job["workspace_id"] == workspace_id:
                nodes = nodes_by_job.get(job_id, [])
                try:
                    actual_node_key = resolve_rerun_node_from_nodes(
                        job_id, job, nodes, node_key, from_failed_node
                    )
                except JobOperationError:
                    pass
                else:
                    if (
                        rerun_ineligible_from_nodes(
                            definitions.for_job(job), nodes, busy_pairs, job_id, actual_node_key
                        )
                        is None
                    ):
                        targets = [actual_node_key]
            planned[job_id] = targets
    plans = [{"job_id": job_id, "planned_targets": planned.get(job_id, [])} for job_id in ids]
    eligible = sum(bool(targets) for targets in planned.values())
    return {"total_count": len(ids), "eligible_count": eligible, "plans": plans}


def _failure_category_plans(
    service: JobRerunService,
    definitions: PreviewDefinitions,
    jobs: dict[str, dict[str, Any]],
    nodes_by_job: dict[str, list[dict[str, Any]]],
    busy_pairs: set[tuple[str, str]],
    workspace_id: str,
    category: str,
    strategy: str,
    from_node_key: str | None,
    ids: list[str],
) -> dict[str, list[str]]:
    grouped = failed_nodes_by_job(service, workspace_id, category, ids)
    planned: dict[str, list[str]] = {}
    for job_id, failed_nodes in grouped.items():
        job = jobs.get(job_id)
        if job is None or job["workspace_id"] != workspace_id:
            continue
        plan = plan_failure_rerun(
            definitions.for_job(job),
            failed_nodes,
            nodes_by_job.get(job_id, []),
            busy_pairs,
            job_id,
            category,
            strategy,
            from_node_key,
        )
        planned[job_id] = plan.planned_targets
    return planned
