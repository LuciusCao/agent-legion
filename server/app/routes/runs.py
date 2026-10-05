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
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from server.app.auth.api_intake import require_workspace_api_intake
from server.app.auth.api_scope_surface import API_SCOPE_INTAKE_TAG
from server.app.auth.dependencies import get_current_user
from server.app.auth.workspace_api_tokens import WORKSPACE_API_SCOPE
from server.app.routes.job_http import raise_job_http_error
from server.app.routes.run_contracts import (
    RunCreateRequest,
    RunCreateResponse,
    RunDetailResponse,
    RunListResponse,
)
from server.app.services.job_errors import JobServiceError
from server.app.services.materials import MaterialStorageUnavailableError
from server.app.services.run_service import RunService

logger = logging.getLogger(__name__)


def create_runs_router(service: RunService) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/workspaces/{workspace_id}/runs",
        response_model=RunCreateResponse,
        dependencies=[Depends(require_workspace_api_intake)],
        tags=[API_SCOPE_INTAKE_TAG],
    )
    def create_run(
        workspace_id: str,
        payload: RunCreateRequest,
        user: Annotated[dict[str, Any], Depends(get_current_user)],
    ) -> RunCreateResponse:
        # #626 audit: an api-token submission attributes to the token id,
        # never an impersonated user — the api-scope identity carries no
        # user id, so created_by below stays empty for the machine channel
        # and only session users are recorded. The request identity lands
        # in the structured log — the same surface the worker registration
        # handshake uses for its audit trail. This first record is the
        # ATTEMPT (pre-validation observability); the success record below
        # only fires after service.create_run actually created the run, so
        # a rejected submission (unknown material,
        # no active revision, duplicate) never logs a success.
        if user.get("actor_scope") == WORKSPACE_API_SCOPE:
            logger.info(
                "run submission attempt via workspace api token: token_id=%s workspace_id=%s",
                user.get("api_token_id"),
                workspace_id,
            )
        # exclude_unset keeps input_json verbatim (no params={} filler).
        body = payload.model_dump(exclude_unset=True)
        try:
            result = service.create_run(
                workspace_id,
                # #211 M3: the workspace id is the workflow identifier (it
                # also feeds the deterministic run id, unchanged across M3).
                workflow_key=workspace_id,
                items=body["items"],
                created_by=str(user.get("id") or ""),
            )
        except MaterialStorageUnavailableError as exc:
            # text items need the object store (same 503 as the materials API).
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except JobServiceError as exc:
            raise_job_http_error(exc)
        if user.get("actor_scope") == WORKSPACE_API_SCOPE:
            logger.info(
                "run submitted via workspace api token: token_id=%s workspace_id=%s run_id=%s",
                user.get("api_token_id"),
                workspace_id,
                result["run"]["id"],
            )
        return RunCreateResponse(**result)

    @router.get(
        "/workspaces/{workspace_id}/runs",
        response_model=RunListResponse,
        tags=[API_SCOPE_INTAKE_TAG],
    )
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

    @router.get(
        "/workspaces/{workspace_id}/runs/{run_id}",
        response_model=RunDetailResponse,
        tags=[API_SCOPE_INTAKE_TAG],
    )
    def get_run(workspace_id: str, run_id: str) -> RunDetailResponse:
        try:
            return RunDetailResponse(**service.get_run(workspace_id, run_id))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
