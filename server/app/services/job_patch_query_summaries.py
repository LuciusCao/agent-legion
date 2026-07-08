from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_queries import JobQueryService
from server.app.workflows.definition import WorkflowDefinition


def summarize_paginated_jobs(
    query_service: JobQueryService,
    job_db: JobQueries,
    jobs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not jobs:
        return []
    job_ids = [str(job["id"]) for job in jobs]
    nodes_by_job = job_db.list_job_nodes_for_jobs(job_ids)
    definitions: dict[tuple[str, str], WorkflowDefinition] = {}

    def _definition(job: dict[str, Any]) -> WorkflowDefinition:
        key = (str(job["workflow_key"]), str(job.get("workflow_definition_hash") or ""))
        if key not in definitions:
            definitions[key] = query_service._definition_for_job(job)
        return definitions[key]

    return [
        query_service._job_summary(job, nodes_by_job.get(str(job["id"]), []), _definition(job))
        for job in jobs
    ]
