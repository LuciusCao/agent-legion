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
from server.app.auth.workspace_api_tokens import WORKSPACE_API_SCOPE
from server.app.routes.run_contracts import (
    RunAcceptedResponse,
    RunBatchResponse,
    RunCreateRequest,
    RunCreateResponse,
    RunDetailResponse,
    RunListResponse,
)
from server.app.services.materials import MaterialStorageUnavailableError
from server.app.services.run_service import RunService

logger = logging.getLogger(__name__)


# #626 audit: an api-token submission attributes to the token id, never an
# impersonated user (the api-scope identity carries no user id, so created_by
# stays empty for the machine channel). The attempt record is pre-validation
# observability; the success record fires only after the service actually
# accepted/created the run, so a rejected submission (unknown material, no
# active revision, duplicate) never logs a success.
def _log_api(user: dict[str, Any], verb: str, workspace_id: str, run_id: str | None = None) -> None:
    if user.get("actor_scope") != WORKSPACE_API_SCOPE:
        return
    # The attempt record carries no run_id (pre-validation); the success
    # record always does — pinned by test_workspace_api_token_boundaries.py.
    suffix = f" run_id={run_id}" if run_id is not None else ""
    logger.info(
        "%s via workspace api token: token_id=%s workspace_id=%s%s",
        verb,
        user.get("api_token_id"),
        workspace_id,
        suffix,
    )


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
        _log_api(user, "run submission attempt", workspace_id)
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
                _log_api(user, "run accepted (async)", workspace_id, accepted["run"]["id"])
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
        _log_api(user, "run submitted", workspace_id, result["run"]["id"])
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

    # #1117 async-mode control plane: feeder-only pause/resume/cancel (jobs
    # already created stay; the job batch ops manage them). Registered via
    # add_api_route so the three verbs share one declaration; endpoint names
    # stay distinct for the api-scope manifest (#734 contract test).
    def pause_run_batch(workspace_id: str, run_id: str) -> RunBatchResponse:
        return RunBatchResponse(**service.pause_run_batch(workspace_id, run_id))

    def resume_run_batch(workspace_id: str, run_id: str) -> RunBatchResponse:
        return RunBatchResponse(**service.resume_run_batch(workspace_id, run_id))

    def cancel_run_batch(workspace_id: str, run_id: str) -> RunBatchResponse:
        return RunBatchResponse(**service.cancel_run_batch(workspace_id, run_id))

    for _action, _endpoint in (
        ("pause", pause_run_batch),
        ("resume", resume_run_batch),
        ("cancel", cancel_run_batch),
    ):
        router.add_api_route(
            f"/workspaces/{{workspace_id}}/runs/{{run_id}}/{_action}",
            _endpoint,
            methods=["POST"],
            response_model=RunBatchResponse,
            dependencies=[Depends(require_workspace_api_intake)],
            tags=[API_SCOPE_INTAKE_TAG],
        )

    return router
