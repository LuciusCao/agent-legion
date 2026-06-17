from fastapi import APIRouter

from server.app.routes.job_contracts import (
    WorkspaceSettingsResponse,
    WorkspaceSettingsSectionRequest,
    WorkspaceSettingsTestResponse,
)
from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.services.job_errors import JobServiceError
from server.app.services.workspace_configuration import WorkspaceConfigurationService
from server.app.settings import Settings


def create_workspace_settings_router(
    service: WorkspaceConfigurationService, settings: Settings
) -> APIRouter:
    router = APIRouter()

    @router.get("/workspaces/{workspace_id}/settings", response_model=WorkspaceSettingsResponse)
    def get_workspace_settings(workspace_id: str) -> WorkspaceSettingsResponse:
        require_workflows_enabled(settings)
        try:
            return WorkspaceSettingsResponse(settings=service.settings_payload(workspace_id))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.patch(
        "/workspaces/{workspace_id}/settings/{section}",
        response_model=WorkspaceSettingsResponse,
    )
    def update_workspace_settings_section(
        workspace_id: str,
        section: str,
        payload: WorkspaceSettingsSectionRequest,
    ) -> WorkspaceSettingsResponse:
        require_workflows_enabled(settings)
        try:
            return WorkspaceSettingsResponse(
                settings=service.update_section(
                    workspace_id, section, payload.model_dump(exclude_unset=True)
                )
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.post(
        "/workspaces/{workspace_id}/settings/test-connection",
        response_model=WorkspaceSettingsTestResponse,
    )
    def test_workspace_connection(workspace_id: str) -> WorkspaceSettingsTestResponse:
        require_workflows_enabled(settings)
        try:
            return WorkspaceSettingsTestResponse(**service.test_connection(workspace_id))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
