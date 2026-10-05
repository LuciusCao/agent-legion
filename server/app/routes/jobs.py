from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Query

from server.app.auth.api_scope_surface import API_SCOPE_INTAKE_TAG
from server.app.routes.job_http import (
    reject_mismatched_workflow_key,
)
from server.app.routes.job_view_contracts import (
    JobDetailResponse,
    JobsResponse,
    JobSummaryResponse,
)
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

    # #272: legacy list endpoint (the frontend already uses the paginated
    # /jobs/snapshot). select * carries KB-scale TEXT columns, so the
    # response stays bounded: limit defaults to the historical fixed cap
    # (500) and is contract-bounded to [1, 2000] by Query below; JobQueries
    # .list_jobs keeps its own clamp (with probe headroom, see job_nodes.py).
    # #735 review P2-1: truncation is NEVER silent — the route probes
    # limit+1 rows and sets the response's `truncated` when the bound cut
    # anything. run_id turns this endpoint into a per-run read for external
    # callers (a busy workspace's unfiltered list stays unreliable at the
    # default bound); a run larger than the 2000 ceiling is reconciled
    # through the POST /runs response's job_ids (the primary loop — poll
    # each id on #703's per-job endpoints). Filter semantics, not resource
    # addressing: a run_id from another workspace (or a missing run) yields
    # an empty list, not a 404.
    # #735 review P2 (cluster): an optional filter's EMPTY-STRING form is a
    # caller error, never "filter off" — `?run_id=` / `?status=` would
    # otherwise be swallowed by the query layer's `if val` and silently
    # return the unfiltered list. min_length=1 turns that shape into a 422;
    # None (param absent) remains the only "no filter" spelling.
    @router.get(
        "/workspaces/{workspace_id}/jobs",
        response_model=JobsResponse,
        tags=[API_SCOPE_INTAKE_TAG],
    )
    def list_workspace_jobs(
        workspace_id: str,
        workflow_key: Annotated[
            str | None,
            Query(deprecated=True, description=_DEPRECATED_QUERY),
        ] = None,
        status: Annotated[str | None, Query(min_length=1)] = None,
        run_id: Annotated[str | None, Query(min_length=1)] = None,
        limit: Annotated[int, Query(ge=1, le=2000)] = 500,
    ) -> JobsResponse:
        # Subagent review P3-1 on #307: guard parity with failed-node-runs —
        # a mismatched key can no longer narrow (the column filter is the
        # next read-binding batch); reject instead of silently widening.
        reject_mismatched_workflow_key(workspace_id, workflow_key)
        # limit+1 probe: the extra row decides `truncated` before the
        # response is cut to the requested bound.
        jobs = job_queries.list_jobs(
            workspace_id,
            workflow_key=workflow_key,
            status=status,
            run_id=run_id,
            limit=limit + 1,
        )
        truncated = len(jobs) > limit
        return JobsResponse(
            jobs=cast(list[JobSummaryResponse], jobs[:limit] if truncated else jobs),
            truncated=truncated,
        )

    @router.get("/jobs/{job_id}", response_model=JobDetailResponse)
    def get_job(job_id: str) -> JobDetailResponse:
        # Legacy bare route（#631 攻击审查 H2 曾以路由级 scoped-404 收口；
        # rebase #745 后该守卫的前提过时——job_group 的
        # require_job_workspace_access 现在按 job 行反查授权域，裸路由与
        # /workspaces/{ws}/jobs/{job_id} 前缀家族同一语义：scoped 绑定
        # token 只读绑定 workspace（跨域/未知 job 一律 404），成员按
        # membership，全会话 admin 走 fast path）。
        return JobDetailResponse(**job_queries.detail(job_id))

    return router
