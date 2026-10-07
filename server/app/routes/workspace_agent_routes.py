from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from server.app.routes.workspace_execution_contracts import (
    WorkspaceAgentProvenanceEntry,
    WorkspaceAgentProvenanceResponse,
    WorkspaceAgentRouteEntry,
    WorkspaceAgentRoutesResponse,
)
from server.app.services.workspace_agent_provenance import list_workspace_agent_provenance
from server.app.services.workspace_agent_routes import list_workspace_agent_routes

if TYPE_CHECKING:
    from server.app.jobs.queries import JobQueries


def create_workspace_agent_routes_router(job_db: JobQueries) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/workspaces/{workspace_id}/agent-routes",
        response_model=WorkspaceAgentRoutesResponse,
    )
    def get_workspace_agent_routes(workspace_id: str) -> WorkspaceAgentRoutesResponse:
        return WorkspaceAgentRoutesResponse(
            routes=[
                WorkspaceAgentRouteEntry(**route)
                for route in list_workspace_agent_routes(job_db, workspace_id)
            ]
        )

    # #1079（#440 D1）：设置页「历史 Agent 定义」的「已内联到 N 个节点」——
    # 只读 active revision 的 agent_profile_provenance。
    @router.get(
        "/workspaces/{workspace_id}/agent-provenance",
        response_model=WorkspaceAgentProvenanceResponse,
    )
    def get_workspace_agent_provenance(workspace_id: str) -> WorkspaceAgentProvenanceResponse:
        return WorkspaceAgentProvenanceResponse(
            nodes=[
                WorkspaceAgentProvenanceEntry(**entry)
                for entry in list_workspace_agent_provenance(job_db, workspace_id)
            ]
        )

    return router
