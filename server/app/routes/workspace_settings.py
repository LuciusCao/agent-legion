from fastapi import APIRouter

from server.app.routes.job_contracts import (
    WorkspaceConfigurationRequest,
    WorkspaceConfigurationResponse,
    WorkspaceSettingsResponse,
    WorkspaceSettingsSectionRequest,
    WorkspaceSettingsTestResponse,
)
from server.app.routes.job_http import raise_job_http_error, require_pipelines_enabled
from server.app.services.job_errors import JobServiceError
from server.app.services.workspace_configuration import WorkspaceConfigurationService
from server.app.settings import Settings


def create_workspace_settings_router(
    service: WorkspaceConfigurationService, settings: Settings
) -> APIRouter:
    router = APIRouter()

    @router.get("/workspaces/{workspace_id}/settings", response_model=WorkspaceSettingsResponse)
    def get_workspace_settings(workspace_id: str) -> WorkspaceSettingsResponse:
        require_pipelines_enabled(settings)
        try:
            return WorkspaceSettingsResponse(settings=service.settings_payload(workspace_id))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.put(
        "/workspaces/{workspace_id}/configuration",
        response_model=WorkspaceConfigurationResponse,
    )
    def replace_workspace_configuration(
        workspace_id: str,
        payload: WorkspaceConfigurationRequest,
    ) -> WorkspaceConfigurationResponse:
        require_pipelines_enabled(settings)
        try:
            result = service.replace_configuration(
                workspace_id,
                workspace_patch=payload.model_dump(
                    include={"name", "description"}, exclude_unset=True
                ),
                settings_patch=payload.settings.model_dump(exclude_unset=True),
                executor_allocations=[a.model_dump() for a in payload.executor_allocations],
                node_bindings=[b.model_dump() for b in payload.node_bindings],
                node_limits=[n.model_dump() for n in payload.node_limits],
            )
            return WorkspaceConfigurationResponse(**result)
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
        require_pipelines_enabled(settings)
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
        require_pipelines_enabled(settings)
        try:
            return WorkspaceSettingsTestResponse(**service.test_connection(workspace_id))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
