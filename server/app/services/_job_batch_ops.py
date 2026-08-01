"""Batch delete/run-to loops over selections resolved from ids or a list filter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.services.job_selection_resolver import resolve_batch_selection

if TYPE_CHECKING:
    from collections.abc import Collection

    from server.app.services.job_deletion import JobDeleteResult, JobDeletionService
    from server.app.services.job_execution import JobExecutionService


def batch_delete(
    service: JobDeletionService,
    workspace_id: str,
    job_ids: list[str] | None = None,
    *,
    job_filter: JobListFilter | None = None,
    exclude_ids: Collection[str] = (),
) -> list[JobDeleteResult]:
    """Delete each selected job; explicit ids and filters resolve the same way."""
    ids = resolve_batch_selection(service.job_db, workspace_id, job_ids, job_filter, exclude_ids)
    results: list[JobDeleteResult] = []
    seen: set[str] = set()
    for job_id in ids:
        normalized = job_id.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        results.append(service.delete(workspace_id, normalized))
    return results


def batch_run_to(
    service: JobExecutionService,
    workspace_id: str,
    job_ids: list[str] | None,
    target_node_key: str,
    start_node_key: str | None = None,
    *,
    job_filter: JobListFilter | None = None,
    exclude_ids: Collection[str] = (),
) -> list[dict[str, Any]]:
    """Run each selected job to ``target_node_key`` (ids are not normalized)."""
    ids = resolve_batch_selection(service.job_db, workspace_id, job_ids, job_filter, exclude_ids)
    return [service.run_to(workspace_id, job_id, target_node_key, start_node_key) for job_id in ids]
