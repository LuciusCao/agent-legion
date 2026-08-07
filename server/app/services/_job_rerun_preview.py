"""Read-only batch-rerun preview: eligible/total counts for a selection.

Set-based, not per-job: the selection is materialized with one page scan,
then jobs / job nodes / active leases are fetched with one bulk query each
and joined in memory, so a multi-thousand-job selection costs a constant
number of round trips instead of several per job. The per-job predicates live
in ``_job_rerun_preview_checks`` (pure bulk-data equivalents of the write
path's checks). Nothing in this module writes.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import TYPE_CHECKING, Any

from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.services._job_rerun_eligibility import (
    AUTO_STRATEGIES,
    failed_nodes_by_job,
    resolve_failure_rerun_targets,
)
from server.app.services._job_rerun_preview_checks import (
    PreviewDefinitions,
    rerun_ineligible_from_nodes,
    resolve_rerun_node_from_nodes,
)
from server.app.services.job_operation_error import JobOperationError
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
    if not ids:
        return {"total_count": 0, "eligible_count": 0}

    # One bulk query per data domain; the per-job checks below join in memory.
    # Narrow column sets keep row materialization cheap on large selections.
    jobs = service.job_db.list_job_rerun_states_for_jobs(workspace_id, ids)
    nodes_by_job = service.job_db.list_job_node_states_for_jobs(ids)
    busy_pairs = service.lease_repo.active_lease_node_keys_for_jobs(ids, service._now())
    definitions = PreviewDefinitions(service)

    if failure_category is not None:
        eligible = _failure_category_eligible_count(
            service,
            definitions,
            jobs,
            nodes_by_job,
            busy_pairs,
            workspace_id,
            failure_category,
            ids,
        )
    else:
        eligible = 0
        for job_id in ids:
            job = jobs.get(job_id)
            if job is None or job["workspace_id"] != workspace_id:
                continue
            nodes = nodes_by_job.get(job_id, [])
            try:
                actual_node_key = resolve_rerun_node_from_nodes(
                    job_id, job, nodes, node_key, from_failed_node
                )
            except JobOperationError:
                continue
            if (
                rerun_ineligible_from_nodes(
                    definitions.for_job(job), nodes, busy_pairs, job_id, actual_node_key
                )
                is None
            ):
                eligible += 1
    return {"total_count": len(ids), "eligible_count": eligible}


def _failure_category_eligible_count(
    service: JobRerunService,
    definitions: PreviewDefinitions,
    jobs: dict[str, dict[str, Any]],
    nodes_by_job: dict[str, list[dict[str, Any]]],
    busy_pairs: set[tuple[str, str]],
    workspace_id: str,
    category: str,
    ids: Sequence[str],
) -> int:
    """Jobs whose category rerun would rerun at least one node.

    ``from_node_key`` is not part of the allMatching dialog flow, so it is
    not modelled here.
    """
    grouped = failed_nodes_by_job(service, workspace_id, category, ids)
    strategy = AUTO_STRATEGIES.get(category, "rerun_self")
    count = 0
    for job_id, failed_nodes in grouped.items():
        job = jobs.get(job_id)
        if job is None or job["workspace_id"] != workspace_id:
            continue
        definition = definitions.for_job(job)
        targets = resolve_failure_rerun_targets(definition, failed_nodes, strategy)
        if any(
            rerun_ineligible_from_nodes(
                definition, nodes_by_job.get(job_id, []), busy_pairs, job_id, target
            )
            is None
            for target in targets
        ):
            count += 1
    return count
