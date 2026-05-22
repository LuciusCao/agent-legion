import asyncio
import json
import subprocess
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket


@dataclass
class AgentStatus:
    id: str
    name: str
    busy: bool
    current_video_id: str | None = None


class AgentStatusManager:
    def __init__(self) -> None:
        self.agents: list[AgentStatus] = []
        self._clients: set[WebSocket] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def discover(self) -> list[AgentStatus]:
        try:
            result = subprocess.run(
                ["openclaw", "agents", "list", "--json"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return []
            data = json.loads(result.stdout)
            self.agents = [
                AgentStatus(
                    id=a["id"],
                    name=a.get("identityName") or a["id"],
                    busy=False,
                )
                for a in data
                if isinstance(a, dict) and "id" in a
            ]
        except Exception:
            self.agents = []
        return list(self.agents)

    def get_all(self) -> list[AgentStatus]:
        return list(self.agents)

    def set_busy(self, agent_id: str, video_id: str) -> None:
        for agent in self.agents:
            if agent.id == agent_id:
                agent.busy = True
                agent.current_video_id = video_id
                break
        self._broadcast()

    def set_idle(self, agent_id: str) -> None:
        for agent in self.agents:
            if agent.id == agent_id:
                agent.busy = False
                agent.current_video_id = None
                break
        self._broadcast()

    def to_dicts(self) -> list[dict[str, Any]]:
        return [
            {
                "id": a.id,
                "name": a.name,
                "busy": a.busy,
                "current_video_id": a.current_video_id,
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
