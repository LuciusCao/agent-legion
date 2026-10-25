"""Run-batches task-center routes (#1157 phase3).

``GET /workspaces/{id}/run-batches`` feeds the Studio task center (three
views + badge count); ``POST .../run-batches/{run_id}/archive`` is the
manual archive. Both are also part of the #626 machine loop (submit → poll
→ cancel → archive), so they carry the intake tag and are registered in
``API_SCOPE_INTAKE_ROUTE_NAMES`` (auth/api_scope_surface.py) — the GET
passes ``require_workspace_access``'s api-scope arm like the runs GETs, the
archive POST mounts ``require_workspace_api_intake`` like the #1117 control
plane. Design: docs/architecture/runs-async-mode.md §6.

The router carries ``require_workspace_access`` itself (not the job_group
mount): the membership/api-scope semantics for workspace-prefixed routes
are identical, and the module stays mountable from main.py.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request

from server.app.auth.api_intake import require_workspace_api_intake
from server.app.auth.api_scope_surface import API_SCOPE_INTAKE_TAG
from server.app.auth.workspace_access import require_workspace_access
from server.app.jobs import JobQueries
from server.app.routes.run_batches_contracts import (
    RunBatchArchiveResponse,
    RunBatchListResponse,
)
from server.app.services import run_batch_listing


def create_run_batches_router(job_db: JobQueries) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_workspace_access)])

    @router.get(
        "/workspaces/{workspace_id}/run-batches",
        response_model=RunBatchListResponse,
        tags=[API_SCOPE_INTAKE_TAG],
    )
    def list_run_batches(
        request: Request,
        workspace_id: str,
        view: Literal["active", "ended", "archived"] = "active",
        limit: Annotated[int, Query(ge=1, le=200)] = 20,
        cursor: str | None = None,
    ) -> RunBatchListResponse:
        return RunBatchListResponse(
            **run_batch_listing.list_run_batches(
                job_db,
                workspace_id,
                settings=request.app.state.settings,
                view=view,
                limit=limit,
                cursor=cursor,
            )
        )

    @router.post(
        "/workspaces/{workspace_id}/run-batches/{run_id}/archive",
        response_model=RunBatchArchiveResponse,
        dependencies=[Depends(require_workspace_api_intake)],
        tags=[API_SCOPE_INTAKE_TAG],
    )
    def archive_run_batch(workspace_id: str, run_id: str) -> RunBatchArchiveResponse:
        return RunBatchArchiveResponse(
            **run_batch_listing.archive_run_batch(job_db, workspace_id, run_id)
        )

    return router
