"""Workspace-member read API for confirming shared resources before a run."""

from __future__ import annotations

from fastapi import APIRouter

from server.app.routes.job_http import raise_job_http_error
from server.app.routes.run_shared_resource_contracts import (
    SharedResourceRunConfirmationResponse,
)
from server.app.services.job_errors import JobServiceError
from server.app.services.run_shared_resources import RunSharedResourcesService


def create_run_shared_resources_router(service: RunSharedResourcesService) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/workspaces/{workspace_id}/shared-resource-run-confirmation",
        response_model=SharedResourceRunConfirmationResponse,
    )
    def get_run_confirmation(workspace_id: str) -> SharedResourceRunConfirmationResponse:
        try:
            return SharedResourceRunConfirmationResponse.model_validate(
                service.get_confirmation(workspace_id)
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
