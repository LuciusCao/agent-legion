from typing import Annotated

from fastapi import APIRouter, Query

from server.app.jobs import JobQueries
from server.app.routes.agent_catalog_contracts import AgentCatalogResponse
from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.routes.skill_catalog_route import create_skill_catalog_router
from server.app.routes.workspace_execution_contracts import (
    WorkspaceExecutionConfigurationResponse,
)
from server.app.services.agent_catalog_projection import AgentCatalogService
from server.app.services.job_errors import JobServiceError
from server.app.services.workspace_execution_configuration import (
    WorkspaceExecutionConfigurationService,
)
from server.app.settings import Settings


def create_workspace_agent_catalog_router(
    catalog: AgentCatalogService,
    workspace_configuration: WorkspaceExecutionConfigurationService,
    settings: Settings,
    job_db: JobQueries | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/agent-catalog", response_model=AgentCatalogResponse)
    def get_agent_catalog(workspace_id: Annotated[str, Query()]) -> AgentCatalogResponse:
        # The Agent half of the catalog is workspace-scoped (schema v46), so
        # the required workspace_id query parameter doubles as the membership
        # scope enforced by the router-level workspace-access dependency.
        require_workflows_enabled(settings)
        return AgentCatalogResponse(**catalog.catalog(workspace_id))

    @router.get(
        "/workspaces/{workspace_id}/execution-configuration",
        response_model=WorkspaceExecutionConfigurationResponse,
    )
    def get_workspace_execution_configuration(
        workspace_id: str,
    ) -> WorkspaceExecutionConfigurationResponse:
        require_workflows_enabled(settings)
        try:
            return WorkspaceExecutionConfigurationResponse(
                **workspace_configuration.get(workspace_id)
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)

    router.include_router(create_skill_catalog_router(settings, job_db))
    return router
