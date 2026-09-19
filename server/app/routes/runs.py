"""Workspace runs API (materials-and-runs design §4, slice 3).

``POST /workspaces/{id}/runs`` creates a run from items (one job per item);
the GET endpoints list and inspect runs. The legacy node-execution listing
lives at ``/workspaces/{id}/node-runs`` (routes/workspace_runs.py).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.auth.workspace_access import require_workspace_access
from server.app.routes.job_http import (
    raise_job_http_error,
    reject_mismatched_workflow_key,
)
from server.app.routes.run_contracts import (
    RunCreateRequest,
    RunCreateResponse,
    RunDetailResponse,
    RunListResponse,
)
from server.app.services.job_errors import JobServiceError
from server.app.services.materials import MaterialStorageUnavailableError
from server.app.services.run_service import RunService


def create_runs_router(service: RunService) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/workspaces/{workspace_id}/runs",
        response_model=RunCreateResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def create_run(
        workspace_id: str,
        payload: RunCreateRequest,
        user: Annotated[dict[str, Any], Depends(require_workspace_access)],
    ) -> RunCreateResponse:
        # exclude_unset keeps input_json verbatim (no params={} filler); the
        # same dump feeds the deprecated workflow_key read (accessing the
        # field attribute itself would raise the deprecation warning, which
        # the test suite escalates to an error).
        # #211 Phase 2: absent workflow_key defaults to the path workspace_id
        # (equal since v62).
        body = payload.model_dump(exclude_unset=True)
        # Codex P1 on #307: a mismatched explicit key would flow verbatim
        # into runs/jobs rows (violating the v62 binding) — reject before
        # the service call.
        reject_mismatched_workflow_key(workspace_id, body.get("workflow_key"))
        try:
            result = service.create_run(
                workspace_id,
                workflow_key=body.get("workflow_key") or workspace_id,
                items=body["items"],
                created_by=str(user.get("id") or ""),
            )
        except MaterialStorageUnavailableError as exc:
            # text items need the object store (same 503 as the materials API).
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return RunCreateResponse(**result)

    @router.get("/workspaces/{workspace_id}/runs", response_model=RunListResponse)
    def list_runs(
        workspace_id: str,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> RunListResponse:
        try:
            return RunListResponse.model_validate(
                {"runs": service.list_runs(workspace_id, limit=limit)}
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.get("/workspaces/{workspace_id}/runs/{run_id}", response_model=RunDetailResponse)
    def get_run(workspace_id: str, run_id: str) -> RunDetailResponse:
        try:
            return RunDetailResponse(**service.get_run(workspace_id, run_id))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
