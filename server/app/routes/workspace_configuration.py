from fastapi import APIRouter, Depends

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.routes.workspace_execution_contracts import (
    WorkspaceConfigurationRequest,
    WorkspaceConfigurationResponse,
)
from server.app.services.job_errors import JobServiceError
from server.app.services.workspace_configuration import WorkspaceConfigurationService
from server.app.settings import Settings


def create_workspace_configuration_router(
    service: WorkspaceConfigurationService, settings: Settings
) -> APIRouter:
    router = APIRouter()

    @router.put(
        "/workspaces/{workspace_id}/configuration",
        response_model=WorkspaceConfigurationResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def replace_workspace_configuration(
        workspace_id: str,
        payload: WorkspaceConfigurationRequest,
    ) -> WorkspaceConfigurationResponse:
        require_workflows_enabled(settings)
        try:
            result = service.replace_configuration(
                workspace_id,
                workspace_patch=payload.model_dump(
                    include={"name", "description"}, exclude_unset=True
                ),
                settings_patch=payload.settings.model_dump(exclude_unset=True),
                node_limits=[n.model_dump() for n in payload.node_limits],
                agent_capacity=payload.agent_capacity,
            )
            return WorkspaceConfigurationResponse(**result)
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
