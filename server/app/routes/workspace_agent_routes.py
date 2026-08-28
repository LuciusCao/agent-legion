from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from server.app.routes.job_http import require_workflows_enabled
from server.app.routes.workspace_execution_contracts import (
    WorkspaceAgentRouteEntry,
    WorkspaceAgentRoutesResponse,
)
from server.app.services.workspace_agent_routes import list_workspace_agent_routes
from server.app.settings import Settings

if TYPE_CHECKING:
    from server.app.jobs.queries import JobQueries


def create_workspace_agent_routes_router(job_db: JobQueries, settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/workspaces/{workspace_id}/agent-routes",
        response_model=WorkspaceAgentRoutesResponse,
    )
    def get_workspace_agent_routes(workspace_id: str) -> WorkspaceAgentRoutesResponse:
        require_workflows_enabled(settings)
        return WorkspaceAgentRoutesResponse(
            routes=[
                WorkspaceAgentRouteEntry(**route)
                for route in list_workspace_agent_routes(job_db, workspace_id)
            ]
        )

    return router
