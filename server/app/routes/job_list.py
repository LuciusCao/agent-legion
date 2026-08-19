from __future__ import annotations

from fastapi import APIRouter, HTTPException

from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.routes.job_list_contracts import JobFacetsResponse, JobsPageResponse
from server.app.services.job_errors import JobServiceError
from server.app.services.job_list_queries import JobListQueryService
from server.app.settings import Settings


def _job_list_filter(
    status: str | None,
    search: str | None,
    workflow_version: int | None,
    workflow_version_none: bool,
    active_node_key: str | None,
    packed: int | None,
    paused: bool | None,
) -> JobListFilter:
    if workflow_version is not None and workflow_version_none:
        raise HTTPException(
            status_code=400,
            detail="workflow_version and workflow_version_none are mutually exclusive",
        )
    return JobListFilter(
        status=status,
        search=search,
        workflow_version=workflow_version,
        workflow_version_none=workflow_version_none,
        active_node_key=active_node_key,
        packed=packed,
        paused=paused,
    )


def create_job_list_router(
    job_list_queries: JobListQueryService,
    settings: Settings,
) -> APIRouter:
    router = APIRouter()

    @router.get("/workspaces/{workspace_id}/jobs/snapshot", response_model=JobsPageResponse)
    def snapshot_workspace_jobs(
        workspace_id: str,
        limit: int = 200,
        cursor: str | None = None,
        status: str | None = None,
        search: str | None = None,
        workflow_version: int | None = None,
        workflow_version_none: bool = False,
        active_node_key: str | None = None,
        packed: int | None = None,
        paused: bool | None = None,
    ) -> JobsPageResponse:
        require_workflows_enabled(settings)
        job_filter = _job_list_filter(
            status, search, workflow_version, workflow_version_none, active_node_key, packed, paused
        )
        try:
            safe_limit = max(1, min(limit, 500))
            return JobsPageResponse(
                **job_list_queries.page(workspace_id, job_filter, limit=safe_limit, cursor=cursor)
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.get("/workspaces/{workspace_id}/jobs/facets", response_model=JobFacetsResponse)
    def workspace_job_facets(
        workspace_id: str,
        status: str | None = None,
        search: str | None = None,
        workflow_version: int | None = None,
        workflow_version_none: bool = False,
        active_node_key: str | None = None,
        packed: int | None = None,
        paused: bool | None = None,
    ) -> JobFacetsResponse:
        require_workflows_enabled(settings)
        job_filter = _job_list_filter(
            status, search, workflow_version, workflow_version_none, active_node_key, packed, paused
        )
        try:
            return JobFacetsResponse(**job_list_queries.facets(workspace_id, job_filter))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
