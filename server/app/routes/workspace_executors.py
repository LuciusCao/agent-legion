from fastapi import APIRouter

from server.app.routes.executor_catalog_contracts import ExecutorCatalogResponse
from server.app.routes.executor_contracts import (
    WorkspaceExecutorConfigurationResponse,
)
from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.routes.skill_catalog_route import create_skill_catalog_router
from server.app.services.executor_catalog import ExecutorCatalogService
from server.app.services.job_errors import JobServiceError
from server.app.services.workspace_executor_configuration import (
    WorkspaceExecutorConfigurationService,
)
from server.app.settings import Settings


def create_workspace_executors_router(
    catalog: ExecutorCatalogService,
    workspace_configuration: WorkspaceExecutorConfigurationService,
    settings: Settings,
) -> APIRouter:
    router = APIRouter()

    @router.get("/executors", response_model=ExecutorCatalogResponse)
    def get_executors() -> ExecutorCatalogResponse:
        require_workflows_enabled(settings)
        return ExecutorCatalogResponse(**catalog.catalog())

    @router.get(
        "/workspaces/{workspace_id}/executor-configuration",
        response_model=WorkspaceExecutorConfigurationResponse,
    )
    def get_workspace_executor_configuration(
        workspace_id: str,
    ) -> WorkspaceExecutorConfigurationResponse:
        require_workflows_enabled(settings)
        try:
            return WorkspaceExecutorConfigurationResponse(
                **workspace_configuration.get(workspace_id)
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)

    router.include_router(create_skill_catalog_router(settings))
    return router
