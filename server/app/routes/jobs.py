from __future__ import annotations

from typing import cast

from fastapi import APIRouter

from server.app.routes.job_http import (
    raise_job_http_error,
    require_workflows_enabled,
)
from server.app.routes.job_view_contracts import (
    JobDetailResponse,
    JobsResponse,
    JobSummaryResponse,
)
from server.app.services.job_errors import JobServiceError
from server.app.services.job_queries import JobQueryService
from server.app.settings import Settings


def create_jobs_router(
    job_queries: JobQueryService,
    settings: Settings,
) -> APIRouter:
    router = APIRouter()

    @router.get("/workspaces/{workspace_id}/jobs", response_model=JobsResponse)
    def list_workspace_jobs(
        workspace_id: str,
        workflow_key: str | None = None,
        status: str | None = None,
    ) -> JobsResponse:
        require_workflows_enabled(settings)
        try:
            return JobsResponse(
                jobs=cast(
                    list[JobSummaryResponse],
                    job_queries.list_jobs(workspace_id, workflow_key=workflow_key, status=status),
                )
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.get("/jobs/{job_id}", response_model=JobDetailResponse)
    def get_job(job_id: str) -> JobDetailResponse:
        require_workflows_enabled(settings)
        try:
            return JobDetailResponse(**job_queries.detail(job_id))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
