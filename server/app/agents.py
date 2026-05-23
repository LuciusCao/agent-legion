import asyncio
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket

from server.app.pipeline.runners import list_openclaw_agents


@dataclass
class AgentStatus:
    id: str
    name: str
    busy: bool
    current_video_id: str | None = None
    current_title: str = ""
    current_content_type: str = ""
    current_external_id: str = ""
    current_phase: str = ""


class AgentStatusManager:
    def __init__(self) -> None:
        self.agents: list[AgentStatus] = []
        self._clients: set[WebSocket] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._busy_video_ids: set[str] = set()
        self._agent_video_ids: dict[str, str] = {}

    def discover(self) -> list[AgentStatus]:
        try:
            records = list_openclaw_agents(timeout=10)
            self.agents = [
                AgentStatus(
                    id=r["id"],
                    name=r.get("identityName") or r["id"],
                    busy=False,
                )
                for r in records
            ]
        except Exception:
            self.agents = []
        return list(self.agents)

    def get_all(self) -> list[AgentStatus]:
        return list(self.agents)

    def set_busy(self, agent_id: str, video: str | dict[str, Any]) -> None:
        video_id = video if isinstance(video, str) else str(video.get("id", ""))
        if video_id:
            self._busy_video_ids.add(video_id)
            self._agent_video_ids[agent_id] = video_id
        for agent in self.agents:
            if agent.id == agent_id:
                agent.busy = True
                agent.current_video_id = video_id
                if isinstance(video, dict):
                    agent.current_title = str(video.get("title", ""))
                    agent.current_content_type = str(video.get("content_type", ""))
                    agent.current_external_id = str(video.get("external_id", ""))
                    agent.current_phase = str(video.get("current_phase", ""))
                break
        self._broadcast()

    def set_idle(self, agent_id: str) -> None:
        video_id = self._agent_video_ids.pop(agent_id, "")
        if video_id:
            self._busy_video_ids.discard(video_id)
        for agent in self.agents:
            if agent.id == agent_id:
                agent.busy = False
                agent.current_video_id = None
                agent.current_title = ""
                agent.current_content_type = ""
                agent.current_external_id = ""
                agent.current_phase = ""
                break
        self._broadcast()

    def is_video_busy(self, video_id: str) -> bool:
        return video_id in self._busy_video_ids

    def to_dicts(self) -> list[dict[str, Any]]:
        return [
            {
                "id": a.id,
                "name": a.name,
                "busy": a.busy,
                "current_video_id": a.current_video_id,
                "current_title": a.current_title,
                "current_content_type": a.current_content_type,
                "current_external_id": a.current_external_id,
                "current_phase": a.current_phase,
            }
            for a in self.agents
        ]

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)
        self._loop = asyncio.get_running_loop()
        await websocket.send_json(self.to_dicts())

    def disconnect(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)

    def _broadcast(self) -> None:
        payload = self.to_dicts()
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        for ws in list(self._clients):
            try:
                asyncio.run_coroutine_threadsafe(ws.send_json(payload), loop)
            except Exception:
                self._clients.discard(ws)
