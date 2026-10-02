from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from server.app.auth.api_scope_surface import API_SCOPE_INTAKE_TAG
from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.routes.job_http import raise_job_http_error
from server.app.routes.job_list_contracts import JobFacetsResponse, JobsPageResponse
from server.app.services.job_errors import JobServiceError
from server.app.services.job_list_queries import JobListQueryService

# #735 review P2 (cluster): optional str filters reject the empty-string form
# (422) instead of letting the query layer's truthiness checks silently drop
# the clause and return an unfiltered page — None (absent) is the only "no
# filter" spelling. search/cursor stay unconstrained: an empty search term or
# cursor is an identity no-op (matches everything / first page), never a
# silently-widened filter.
_NonEmptyFilter = Annotated[str | None, Query(min_length=1)]


def _job_list_filter(
    status: str | None,
    search: str | None,
    workflow_version: int | None,
    workflow_version_none: bool,
    active_node_key: str | None,
    packed: int | None,
    paused: bool | None,
    run_id: str | None = None,
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
        run_id=run_id,
    )


def create_job_list_router(
    job_list_queries: JobListQueryService,
) -> APIRouter:
    router = APIRouter()

    # codex3 P1：snapshot 在 api-scope 准入面内（机器调用方越过 legacy
    # 列表上限读全量 job 状态面），挂 intake tag 由 api_scope_surface
    # 派生；同模块的 facets 是前端聚合端点，刻意不挂。
    @router.get(
        "/workspaces/{workspace_id}/jobs/snapshot",
        response_model=JobsPageResponse,
        tags=[API_SCOPE_INTAKE_TAG],
    )
    def snapshot_workspace_jobs(
        workspace_id: str,
        limit: int = 200,
        cursor: str | None = None,
        status: _NonEmptyFilter = None,
        search: str | None = None,
        workflow_version: int | None = None,
        workflow_version_none: bool = False,
        active_node_key: _NonEmptyFilter = None,
        packed: int | None = None,
        paused: bool | None = None,
        run_id: _NonEmptyFilter = None,
    ) -> JobsPageResponse:
        job_filter = _job_list_filter(
            status,
            search,
            workflow_version,
            workflow_version_none,
            active_node_key,
            packed,
            paused,
            run_id,
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
        status: _NonEmptyFilter = None,
        search: str | None = None,
        workflow_version: int | None = None,
        workflow_version_none: bool = False,
        active_node_key: _NonEmptyFilter = None,
        packed: int | None = None,
        paused: bool | None = None,
        run_id: _NonEmptyFilter = None,
    ) -> JobFacetsResponse:
        job_filter = _job_list_filter(
            status,
            search,
            workflow_version,
            workflow_version_none,
            active_node_key,
            packed,
            paused,
            run_id,
        )
        try:
            return JobFacetsResponse(**job_list_queries.facets(workspace_id, job_filter))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
