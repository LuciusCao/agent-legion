import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from starlette import concurrency

from ..auth.dependencies import SESSION_COOKIE, require_user
from ..events.agents import AgentStatusManager

logger = logging.getLogger(__name__)


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

    @router.get("", response_model=AgentsResponse, dependencies=[Depends(require_user)])
    def list_agents() -> AgentsResponse:
        return AgentsResponse(
            agents=[AgentStatusResponse.model_validate(agent) for agent in agent_manager.to_dicts()]
        )

    @router.websocket("")
    async def agents_ws(websocket: WebSocket) -> None:
        # WS handshakes carry the session cookie same-site; router-level
        # dependencies cannot run on websocket scopes, so authenticate here.
        # authenticate() is a synchronous DB read — off the loop, matching the
        # MCP mount's anyio.to_thread handling of the same service.
        auth = websocket.app.state.auth_service
        if (
            await concurrency.run_in_threadpool(
                auth.authenticate, websocket.cookies.get(SESSION_COOKIE, "")
            )
            is None
        ):
            await websocket.close(code=4401)
            return
        await agent_manager.connect(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("Agents websocket receive loop failed")
        finally:
            agent_manager.disconnect(websocket)

    return router
