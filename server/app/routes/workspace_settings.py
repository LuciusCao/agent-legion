from fastapi import APIRouter, Depends

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.routes.job_contracts import (
    WorkspaceSettingsResponse,
    WorkspaceSettingsSectionRequest,
)
from server.app.routes.workspace_secrets import create_workspace_secrets_router
from server.app.services.workspace_configuration import WorkspaceConfigurationService
from server.app.services.workspace_secrets import WorkspaceSecretsService
from server.app.settings import Settings


def create_workspace_settings_router(
    service: WorkspaceConfigurationService, settings: Settings
) -> APIRouter:
    router = APIRouter()

    @router.get("/workspaces/{workspace_id}/settings", response_model=WorkspaceSettingsResponse)
    def get_workspace_settings(workspace_id: str) -> WorkspaceSettingsResponse:
        return WorkspaceSettingsResponse(settings=service.settings_payload(workspace_id))

    @router.patch(
        "/workspaces/{workspace_id}/settings/{section}",
        response_model=WorkspaceSettingsResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def update_workspace_settings_section(
        workspace_id: str,
        section: str,
        payload: WorkspaceSettingsSectionRequest,
    ) -> WorkspaceSettingsResponse:
        return WorkspaceSettingsResponse(
            settings=service.update_section(
                workspace_id, section, payload.model_dump(exclude_unset=True)
            )
        )

    # Vault secrets live in the same workspace settings route family (spec D13).
    router.include_router(
        create_workspace_secrets_router(WorkspaceSecretsService(service.job_db, settings), settings)
    )

    return router
