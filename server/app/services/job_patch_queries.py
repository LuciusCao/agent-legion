from __future__ import annotations

from typing import Any

from server.app.jobs import JobQueries
from server.app.jobs.queries.job_pagination import list_jobs_paginated
from server.app.services.job_patch_query_summaries import summarize_paginated_jobs
from server.app.services.job_queries import JobQueryService
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workspace_executor_configuration import (
    WorkspaceExecutorConfigurationService,
)
from server.app.settings import Settings


class JobPatchQueryService(JobQueryService):
    """Compact query helpers used by the workspace event aggregation path."""

    def __init__(
        self,
        job_db: JobQueries,
        settings: Settings,
        workflows: WorkflowCatalogService | None = None,
        workspace_executor_config: WorkspaceExecutorConfigurationService | None = None,
        job_event_buffer: Any | None = None,
    ):
        workflows = workflows or WorkflowCatalogService(settings)
        workspace_executor_config = (
            workspace_executor_config or WorkspaceExecutorConfigurationService(job_db)
        )
        super().__init__(job_db, settings, workflows, workspace_executor_config)
        self._job_event_buffer = job_event_buffer

    def list_patch_summaries(
        self,
        workspace_id: str,
        job_ids: list[str],
    ) -> list[dict[str, Any]]:
        if not job_ids:
            return []
        jobs = self.job_db.list_jobs_by_ids(workspace_id, job_ids)
        return summarize_paginated_jobs(self, self.job_db, jobs)

    def count_jobs_by_status(self, workspace_id: str) -> dict[str, int]:
        return self.job_db.count_jobs_by_status(workspace_id)

    def snapshot(
        self,
        workspace_id: str,
        limit: int = 200,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        jobs, next_cursor = list_jobs_paginated(self.job_db, workspace_id, limit, cursor)
        jobs = summarize_paginated_jobs(self, self.job_db, jobs)
        revision = getattr(self._job_event_buffer, "_revision", 0) if self._job_event_buffer else 0
        return {
            "workspace_id": workspace_id,
            "revision": revision,
            "stats": self.job_db.count_jobs_by_status(workspace_id),
            "jobs": jobs,
            "next_cursor": next_cursor,
        }
