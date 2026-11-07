"""Machine-intake audit logging and async run-batch route registration."""

import logging
from typing import Any

from fastapi import APIRouter, Depends

from server.app.auth.api_intake import require_workspace_api_intake
from server.app.auth.api_scope_surface import API_SCOPE_INTAKE_TAG
from server.app.auth.workspace_api_tokens import WORKSPACE_API_SCOPE
from server.app.routes.run_contracts import RunBatchResponse
from server.app.services.run_service import RunService


def log_api(
    logger: logging.Logger,
    user: dict[str, Any],
    verb: str,
    workspace_id: str,
    run_id: str | None = None,
) -> None:
    # #626 audit: an api-token submission attributes to the token id, never an
    # impersonated user (the api-scope identity carries no user id, so created_by
    # stays empty for the machine channel). The attempt record is pre-validation
    # observability; the success record fires only after the service actually
    # accepted/created the run, so a rejected submission (unknown material, no
    # active revision, duplicate) never logs a success.
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


def register_run_batch_controls(router: APIRouter, service: RunService) -> None:
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
