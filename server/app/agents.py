import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket

from server.app.pipeline.runners import list_openclaw_agents

if TYPE_CHECKING:
    from server.app.db import Database


@dataclass
class AgentStatus:
    id: str
    name: str
    busy: bool
    task_count: int = 0
    max_tasks: int = 1
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
        self._agent_video_ids: dict[str, list[str]] = {}
        self._workspace_assignments: dict[str, dict[str, int]] = {}

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

    def set_runner_counts(self, runner_counts: dict[str, int]) -> None:
        for agent in self.agents:
            agent.max_tasks = runner_counts.get(agent.id, 1)

    def load_workspace_assignments(self, db: "Database") -> None:
        self._workspace_assignments = {}
        for row in db.list_workspace_agents("video-hive"):
            self._workspace_assignments.setdefault("video-hive", {})[row["agent_id"]] = row[
                "concurrency_limit"
            ]

    def set_workspace_assignment(
        self, workspace_id: str, agent_id: str, concurrency_limit: int = 1
    ) -> None:
        self._workspace_assignments.setdefault(workspace_id, {})[agent_id] = concurrency_limit

    def remove_workspace_assignment(self, workspace_id: str, agent_id: str) -> None:
        self._workspace_assignments.get(workspace_id, {}).pop(agent_id, None)

    def get_allowed_agents(self, workspace_id: str) -> list[str]:
        return list(self._workspace_assignments.get(workspace_id, {}).keys())

    def is_agent_allowed(self, workspace_id: str, agent_id: str) -> bool:
        return agent_id in self._workspace_assignments.get(workspace_id, {})

    def get_all(self) -> list[AgentStatus]:
        return list(self.agents)

    def set_busy(self, agent_id: str, video: str | dict[str, Any]) -> None:
        video_id = video if isinstance(video, str) else str(video.get("id", ""))
        if video_id:
            self._busy_video_ids.add(video_id)
            self._agent_video_ids.setdefault(agent_id, []).append(video_id)
        for agent in self.agents:
            if agent.id == agent_id:
                agent.task_count += 1
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
        video_ids = self._agent_video_ids.get(agent_id, [])
        video_id = video_ids.pop() if video_ids else ""
        if video_ids:
            self._agent_video_ids[agent_id] = video_ids
        else:
            self._agent_video_ids.pop(agent_id, None)
        if video_id:
            self._busy_video_ids.discard(video_id)
        for agent in self.agents:
            if agent.id == agent_id:
                agent.task_count = max(0, agent.task_count - 1)
                agent.busy = agent.task_count > 0
                if agent.task_count == 0:
                    agent.current_video_id = None
                    agent.current_title = ""
                    agent.current_content_type = ""
                    agent.current_external_id = ""
                    agent.current_phase = ""
                elif video_ids:
                    agent.current_video_id = video_ids[-1]
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
                "task_count": a.task_count,
                "max_tasks": a.max_tasks,
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
