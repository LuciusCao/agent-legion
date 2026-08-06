"""Read-only batch-rerun preview: eligible/total counts for a selection.

The counts reuse the real execution path's checks (``check_rerun_eligibility``
and the category matching helpers), so a preview matches what a confirm would
actually rerun. Nothing in this module writes.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import TYPE_CHECKING

from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.services._job_rerun_by_failure import _AUTO_STRATEGIES
from server.app.services._job_rerun_eligibility import (
    check_rerun_eligibility,
    failed_nodes_by_job,
    resolve_failure_rerun_targets,
)
from server.app.services._job_rerun_single import resolve_rerun_node
from server.app.services.job_operation_error import JobOperationError
from server.app.services.job_selection_resolver import resolve_batch_selection
from server.app.services.workflow_revision_format import definition_from_job_snapshot

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
    job_filter: JobListFilter | None = None,
    exclude_ids: Collection[str] = (),
) -> dict[str, int]:
    """Return {"total_count", "eligible_count"} for the selection; no writes."""
    ids = list(
        dict.fromkeys(
            value.strip()
            for value in resolve_batch_selection(
                service.job_db, workspace_id, job_ids, job_filter, exclude_ids
            )
            if value.strip()
        )
    )
    if failure_category is not None:
        eligible = _failure_category_eligible_count(service, workspace_id, failure_category, ids)
    else:
        eligible = sum(
            1
            for job_id in ids
            if _rerun_eligible(service, workspace_id, job_id, node_key, from_failed_node)
        )
    return {"total_count": len(ids), "eligible_count": eligible}


def _rerun_eligible(
    service: JobRerunService,
    workspace_id: str,
    job_id: str,
    node_key: str | None,
    from_failed_node: bool,
) -> bool:
    job = service.job_db.get_job(job_id)
    if job is None or job["workspace_id"] != workspace_id:
        return False
    try:
        actual_node_key = resolve_rerun_node(
            service.job_db, job_id, job, node_key, from_failed_node
        )
    except JobOperationError:
        return False
    return check_rerun_eligibility(service, job, job_id, actual_node_key) is None


def _failure_category_eligible_count(
    service: JobRerunService,
    workspace_id: str,
    category: str,
    ids: Sequence[str],
) -> int:
    """Jobs whose category rerun would rerun at least one node.

    ``from_node_key`` is not part of the allMatching dialog flow, so it is
    not modelled here.
    """
    grouped = failed_nodes_by_job(service, workspace_id, category, ids)
    strategy = _AUTO_STRATEGIES.get(category, "rerun_self")
    count = 0
    for job_id, nodes in grouped.items():
        job = service.job_db.get_job(job_id)
        if job is None or job["workspace_id"] != workspace_id:
            continue
        definition = definition_from_job_snapshot(job) or service.workflows.definition(
            str(job["workflow_key"])
        )
        targets = resolve_failure_rerun_targets(definition, nodes, strategy)
        if any(check_rerun_eligibility(service, job, job_id, target) is None for target in targets):
            count += 1
    return count
