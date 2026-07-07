from __future__ import annotations

from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_queries import JobQueryService
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workspace_executor_configuration import (
    WorkspaceExecutorConfigurationService,
)
from server.app.settings import Settings
from server.app.workflows.definition import WorkflowDefinition


class JobPatchQueryService(JobQueryService):
    """Compact query helpers used by the workspace event aggregation path."""

    def __init__(
        self,
        job_db: JobQueries,
        settings: Settings,
        workflows: WorkflowCatalogService | None = None,
        workspace_executor_config: WorkspaceExecutorConfigurationService | None = None,
    ):
        workflows = workflows or WorkflowCatalogService(settings)
        workspace_executor_config = (
            workspace_executor_config or WorkspaceExecutorConfigurationService(job_db)
        )
        super().__init__(job_db, settings, workflows, workspace_executor_config)

    def list_patch_summaries(
        self,
        workspace_id: str,
        job_ids: list[str],
    ) -> list[dict[str, Any]]:
        if not job_ids:
            return []
        jobs = [
            job
            for job_id in job_ids
            if (job := self.job_db.get_job(job_id)) is not None
            and str(job.get("workspace_id", "")) == workspace_id
        ]
        nodes_by_job = self.job_db.list_job_nodes_for_jobs([str(job["id"]) for job in jobs])
        definitions: dict[tuple[str, str], WorkflowDefinition] = {}

        def _definition(job: dict[str, Any]) -> WorkflowDefinition:
            key = (str(job["workflow_key"]), str(job.get("workflow_definition_hash") or ""))
            if key not in definitions:
                definitions[key] = self._definition_for_job(job)
            return definitions[key]

        return [
            self._job_summary(job, nodes_by_job.get(str(job["id"]), []), _definition(job))
            for job in jobs
        ]

    def count_jobs_by_status(self, workspace_id: str) -> dict[str, int]:
        return self.job_db.count_jobs_by_status(workspace_id)

    def snapshot(
        self,
        workspace_id: str,
        limit: int = 200,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        del cursor  # reserved for future pagination
        jobs = self.list_jobs(workspace_id)
        bounded = jobs[:limit]
        next_cursor = None
        if len(jobs) > limit and bounded:
            last = bounded[-1]
            next_cursor = f"{last.get('created_at', '')}:{last['id']}"
        return {
            "workspace_id": workspace_id,
            "revision": 0,
            "stats": self.job_db.count_jobs_by_status(workspace_id),
            "jobs": bounded,
            "next_cursor": next_cursor,
        }
