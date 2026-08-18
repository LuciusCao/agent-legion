"""Batch workflow upgrades over selections resolved from ids or a list filter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.services.job_selection_resolver import (
    EmptyJobSelectionError,
    resolve_batch_selection,
)

if TYPE_CHECKING:
    from collections.abc import Collection

    from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService


def batch_upgrade(
    service: JobWorkflowUpgradeService,
    workspace_id: str,
    job_ids: list[str] | None = None,
    *,
    job_filter: JobListFilter | None = None,
    exclude_ids: Collection[str] = (),
) -> list[dict[str, Any]]:
    """Upgrade each selected job; explicit ids and filters resolve the same way."""
    ids = resolve_batch_selection(service.job_db, workspace_id, job_ids, job_filter, exclude_ids)
    if not ids:
        raise EmptyJobSelectionError("No job_ids provided or matched by the filter")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for job_id in ids:
        normalized = job_id.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        results.append(service.upgrade(workspace_id, normalized))
    return results
