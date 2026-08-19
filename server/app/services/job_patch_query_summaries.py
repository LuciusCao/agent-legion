from typing import TYPE_CHECKING, Any

from server.app.jobs import JobQueries
from server.app.services.workflow_definitions import require_workspace_revision_definition
from server.app.workflows.definition import WorkflowDefinition

if TYPE_CHECKING:
    from server.app.services.job_queries import JobQueryService


def summarize_paginated_jobs(
    query_service: "JobQueryService",
    job_db: JobQueries,
    jobs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Summarize jobs with per-workflow caches to avoid N+1 lookups."""
    job_ids = [str(job["id"]) for job in jobs]
    nodes_by_job = job_db.list_job_nodes_for_jobs(job_ids)
    definitions: dict[tuple[str, str], WorkflowDefinition] = {}
    active_revisions: dict[tuple[str, str], dict[str, Any] | None] = {}

    def _active(job: dict[str, Any]) -> dict[str, Any] | None:
        key = (str(job["workspace_id"]), str(job["workflow_key"]))
        if key not in active_revisions:
            active_revisions[key] = job_db.get_active_workflow_revision(*key)
        return active_revisions[key]

    def _definition(job: dict[str, Any]) -> WorkflowDefinition:
        key = (str(job["workflow_key"]), str(job.get("workflow_definition_hash") or ""))
        if key not in definitions:
            if key[1]:
                # Snapshotted job: parse from the frozen snapshot (no query).
                definitions[key] = query_service._definition_for_job(job)
            else:
                # Snapshot-less job: reuse the _active lookup (one query per
                # workspace+key) instead of re-fetching the revision.
                definitions[key] = require_workspace_revision_definition(_active(job))
        return definitions[key]

    return [
        query_service._job_summary(
            job, nodes_by_job.get(str(job["id"]), []), _definition(job), _active(job)
        )
        for job in jobs
    ]
