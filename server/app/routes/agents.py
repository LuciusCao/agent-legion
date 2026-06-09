from typing import Annotated, Any

from fastapi import APIRouter, Depends, WebSocket
from pydantic import BaseModel

from ..agents import AgentStatusManager
from ..db import Database
from .dependencies import get_agent_manager, get_db


class AgentStatusResponse(BaseModel):
    id: str
    name: str
    busy: bool
    current_video_id: str | None = None
    current_title: str = ""
    current_content_type: str = ""
    current_external_id: str = ""
    current_phase: str = ""


class AgentsResponse(BaseModel):
    agents: list[AgentStatusResponse]


class AgentAssignmentResponse(BaseModel):
    agent_id: str
    workspace_id: str
    concurrency_limit: int


class AgentUnassignmentResponse(BaseModel):
    agent_id: str
    workspace_id: str
    removed: bool


def create_agents_router(agent_manager: AgentStatusManager) -> APIRouter:
    router = APIRouter(prefix="/agents", tags=["agents"])

    @router.get("", response_model=AgentsResponse)
    def list_agents() -> dict[str, Any]:
        return {"agents": agent_manager.to_dicts()}

    @router.websocket("")
    async def agents_ws(websocket: WebSocket) -> None:
        await agent_manager.connect(websocket)
        try:
            while True:
                await websocket.receive_text()
        except Exception:
            pass
        finally:
            agent_manager.disconnect(websocket)

    @router.post("/{agent_id}/assign", response_model=AgentAssignmentResponse)
    def assign_agent(
        agent_id: str,
        workspace_id: str,
        db: Annotated[Database, Depends(get_db)],
        manager: Annotated[AgentStatusManager, Depends(get_agent_manager)],
        concurrency_limit: int = 1,
    ) -> dict[str, Any]:
        db.set_workspace_agent_assignment(workspace_id, agent_id, concurrency_limit)
        manager.set_workspace_assignment(workspace_id, agent_id, concurrency_limit)
        return {
            "agent_id": agent_id,
            "workspace_id": workspace_id,
            "concurrency_limit": concurrency_limit,
        }

    @router.delete("/{agent_id}/assign", response_model=AgentUnassignmentResponse)
    def unassign_agent(
        agent_id: str,
        workspace_id: str,
        db: Annotated[Database, Depends(get_db)],
        manager: Annotated[AgentStatusManager, Depends(get_agent_manager)],
    ) -> dict[str, Any]:
        db.remove_workspace_agent_assignment(workspace_id, agent_id)
        manager.remove_workspace_assignment(workspace_id, agent_id)
        return {"agent_id": agent_id, "workspace_id": workspace_id, "removed": True}

    return router
