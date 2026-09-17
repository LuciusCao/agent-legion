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
via the api-scope read allowlist in auth/workspace_access.py (runs + the
jobs listings, legacy and paginated — nothing else on the app is reachable
for the machine identity).
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from server.app.auth.api_intake import require_workspace_api_intake
from server.app.auth.dependencies import get_current_user
from server.app.auth.workspace_api_tokens import WORKSPACE_API_SCOPE
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
from server.app.services.run_service import RunService

logger = logging.getLogger(__name__)


def create_runs_router(service: RunService) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/workspaces/{workspace_id}/runs",
        response_model=RunCreateResponse,
        dependencies=[Depends(require_workspace_api_intake)],
    )
    def create_run(
        workspace_id: str,
        payload: RunCreateRequest,
        user: Annotated[dict[str, Any], Depends(get_current_user)],
    ) -> RunCreateResponse:
        # #626 audit: an api-token submission attributes to the token id,
        # never an impersonated user. The run row's created_by stays with
        # its current semantics (empty for the items path); the request
        # identity lands in the structured log — the same surface the worker
        # registration handshake uses for its audit trail.
        if user.get("actor_scope") == WORKSPACE_API_SCOPE:
            logger.info(
                "run submitted via workspace api token: token_id=%s workspace_id=%s",
                user.get("api_token_id"),
                workspace_id,
            )
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
            )
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
