from fastapi import APIRouter

from server.app.routes.executor_contracts import (
    ExecutorCatalogResponse,
    WorkspaceExecutorConfigurationResponse,
)
from server.app.routes.job_contracts import (
    WorkspaceAgentAssignmentResponse,
    WorkspaceAgentConfig,
    WorkspaceAgentListResponse,
)
from server.app.routes.job_http import raise_job_http_error, require_pipelines_enabled
from server.app.services.executor_catalog import ExecutorCatalogService
from server.app.services.job_errors import JobServiceError
from server.app.settings import Settings


def create_workspace_executors_router(
    service: ExecutorCatalogService, settings: Settings
) -> APIRouter:
    router = APIRouter()

    @router.get("/executors", response_model=ExecutorCatalogResponse)
    def get_executors() -> ExecutorCatalogResponse:
        require_pipelines_enabled(settings)
        return ExecutorCatalogResponse(**service.catalog())

    @router.get(
        "/workspaces/{workspace_id}/executor-configuration",
        response_model=WorkspaceExecutorConfigurationResponse,
    )
    def get_workspace_executor_configuration(
        workspace_id: str,
    ) -> WorkspaceExecutorConfigurationResponse:
        require_pipelines_enabled(settings)
        try:
            return WorkspaceExecutorConfigurationResponse(
                **service.workspace_configuration(workspace_id)
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)

    # Compatibility-only legacy /agents routes retained during migration to Executors.
    @router.get(
        "/workspaces/{workspace_id}/agents",
        response_model=WorkspaceAgentListResponse,
    )
    def get_workspace_agents(workspace_id: str) -> WorkspaceAgentListResponse:
        require_pipelines_enabled(settings)
        try:
            assignments = service.list_assignments(workspace_id)
            return WorkspaceAgentListResponse(
                root=[
                    WorkspaceAgentAssignmentResponse.model_validate(assignment)
                    for assignment in assignments
                ]
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.post(
        "/workspaces/{workspace_id}/agents",
        response_model=WorkspaceAgentAssignmentResponse,
    )
    def set_workspace_agent(
        workspace_id: str,
        config: WorkspaceAgentConfig,
    ) -> WorkspaceAgentAssignmentResponse:
        require_pipelines_enabled(settings)
        try:
            assignment = service.assign(workspace_id, config.agent_id, config.concurrency_limit)
            return WorkspaceAgentAssignmentResponse.model_validate(assignment)
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
