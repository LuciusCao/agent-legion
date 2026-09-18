from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Query

from server.app.routes.job_http import (
    raise_job_http_error,
    reject_mismatched_workflow_key,
)
from server.app.routes.job_view_contracts import (
    JobDetailResponse,
    JobsResponse,
    JobSummaryResponse,
)
from server.app.services.job_errors import JobServiceError
from server.app.services.job_queries import JobQueryService

# #211 Phase 2: query-param deprecation wording (server-side default).
_DEPRECATED_QUERY = (
    "Deprecated: defaults to the workspace id from the path (equal since schema v62); "
    "removal is tracked in #211 (deprecated field drops by 2026-10-31)."
)


def create_jobs_router(
    job_queries: JobQueryService,
) -> APIRouter:
    router = APIRouter()

    # #272: legacy unbounded list endpoint. The frontend already uses the
    # paginated /jobs/snapshot endpoint; this cap is API-compat protection
    # (select * carries KB-scale TEXT columns, so an unbounded response is a
    # memory and latency hazard). The bound lives on JobQueries.list_jobs as a
    # defaulted parameter (clamped to [1, 500] there), so JobQueryService
    # callers inherit it without signature changes. A fixed constant (not a
    # query parameter) keeps the OpenAPI contract and generated frontend
    # types unchanged.
    @router.get("/workspaces/{workspace_id}/jobs", response_model=JobsResponse)
    def list_workspace_jobs(
        workspace_id: str,
        workflow_key: Annotated[
            str | None,
            Query(deprecated=True, description=_DEPRECATED_QUERY),
        ] = None,
        status: str | None = None,
    ) -> JobsResponse:
        # Subagent review P3-1 on #307: guard parity with failed-node-runs —
        # a mismatched key can no longer narrow (the column filter is the
        # next read-binding batch); reject instead of silently widening.
        reject_mismatched_workflow_key(workspace_id, workflow_key)
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
        # Legacy bare route（#631 攻击审查 H2 曾以路由级 scoped-404 收口；
        # rebase #745 后该守卫的前提过时——job_group 的
        # require_job_workspace_access 现在按 job 行反查授权域，裸路由与
        # /workspaces/{ws}/jobs/{job_id} 前缀家族同一语义：scoped 绑定
        # token 只读绑定 workspace（跨域/未知 job 一律 404），成员按
        # membership，全会话 admin 走 fast path）。
        try:
            return JobDetailResponse(**job_queries.detail(job_id))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
