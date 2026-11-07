"""Workspace runs API (materials-and-runs design §4, slice 3).

``POST /workspaces/{id}/runs`` creates a run from items (one job per item);
the GET endpoints list and inspect runs. The legacy node-execution listing
lives at ``/workspaces/{id}/node-runs`` (routes/workspace_runs.py).

#626: the runs surface is also the machine-to-machine intake channel. POST
mounts ``require_workspace_api_intake`` — it admits a workspace API token
(actor_scope='api', bound to this workspace) while refusing every other
scoped identity exactly like the retired ``reject_studio_agent_scope``
mount (studio-agent runs included). The GET endpoints are read-only status
queries the same external callers need: they pass ``require_workspace_access``
via the api-scope intake surface (the route tag + name manifest in
auth/api_scope_surface.py — runs, the jobs listings legacy and paginated,
and the #631 artifact endpoints; nothing else on the app is reachable for
the machine identity).

#1117: ``mode="async"`` accepts with 202 + a run_batches handle (feeder-fed
creation); the pause/resume/cancel control endpoints share the POST guard
(the machine channel owns the whole submit → poll → cancel loop) and are
registered in ``API_SCOPE_INTAKE_ROUTE_NAMES``. Design:
docs/architecture/runs-async-mode.md.
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from server.app.auth.api_intake import require_workspace_api_intake
from server.app.auth.api_scope_surface import API_SCOPE_INTAKE_TAG
from server.app.auth.dependencies import get_current_user
from server.app.routes.run_contracts import (
    RunAcceptedResponse,
    RunCreateRequest,
    RunCreateResponse,
    RunDetailResponse,
    RunListResponse,
)
from server.app.routes.runs_intake import log_api, register_run_batch_controls
from server.app.services.materials import MaterialStorageUnavailableError
from server.app.services.run_service import RunService

logger = logging.getLogger(__name__)


def create_runs_router(service: RunService) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/workspaces/{workspace_id}/runs",
        response_model=RunCreateResponse,
        responses={202: {"model": RunAcceptedResponse, "description": "异步受理句柄"}},
        dependencies=[Depends(require_workspace_api_intake)],
        tags=[API_SCOPE_INTAKE_TAG],
    )
    def create_run(
        workspace_id: str,
        payload: RunCreateRequest,
        user: Annotated[dict[str, Any], Depends(get_current_user)],
    ) -> RunCreateResponse | JSONResponse:
        log_api(logger, user, "run submission attempt", workspace_id)
        # exclude_unset keeps input_json verbatim (no params={} filler).
        items = [item.model_dump(exclude_unset=True) for item in payload.items]
        try:
            if payload.mode == "async":
                # Returning a Response instance bypasses response_model
                # validation (the sync arm keeps its full validation); the
                # 202 schema is declared via responses= for the export.
                accepted = service.create_run_async(
                    workspace_id,
                    workflow_key=workspace_id,
                    items=items,
                    created_by=str(user.get("id") or ""),
                )
                log_api(logger, user, "run accepted (async)", workspace_id, accepted["run"]["id"])
                return JSONResponse(
                    status_code=202, content=RunAcceptedResponse(**accepted).model_dump()
                )
            # #211 M3: the workspace id is the workflow identifier (it also
            # feeds the deterministic run id, unchanged across M3).
            result = service.create_run(
                workspace_id,
                workflow_key=workspace_id,
                items=items,
                created_by=str(user.get("id") or ""),
            )
        except MaterialStorageUnavailableError as exc:
            # text items need the object store (same 503 as the materials API).
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        log_api(logger, user, "run submitted", workspace_id, result["run"]["id"])
        return RunCreateResponse(**result)

    @router.get("/workspaces/{workspace_id}/runs", response_model=RunListResponse, tags=[API_SCOPE_INTAKE_TAG])  # fmt: skip
    def list_runs(
        workspace_id: str, limit: Annotated[int, Query(ge=1, le=500)] = 100
    ) -> RunListResponse:
        return RunListResponse.model_validate(
            {"runs": service.list_runs(workspace_id, limit=limit)}
        )

    @router.get("/workspaces/{workspace_id}/runs/{run_id}", response_model=RunDetailResponse, tags=[API_SCOPE_INTAKE_TAG])  # fmt: skip
    def get_run(workspace_id: str, run_id: str) -> RunDetailResponse:
        return RunDetailResponse(**service.get_run(workspace_id, run_id))

    register_run_batch_controls(router, service)

    return router
