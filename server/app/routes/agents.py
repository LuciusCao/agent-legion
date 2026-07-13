from fastapi import APIRouter, WebSocket
from pydantic import BaseModel

from ..agents import AgentStatusManager


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


def create_agents_router(agent_manager: AgentStatusManager) -> APIRouter:
    router = APIRouter(prefix="/agents", tags=["agents"])

    @router.get("", response_model=AgentsResponse)
    def list_agents() -> AgentsResponse:
        return AgentsResponse(
            agents=[AgentStatusResponse.model_validate(agent) for agent in agent_manager.to_dicts()]
        )

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

    return router
