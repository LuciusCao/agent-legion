import asyncio
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket

from server.app.agent_broadcast import AgentBroadcastController
from server.app.event_bus import _EVICTED, EventBus


@dataclass
class AgentStatus:
    id: str
    name: str
    busy: bool
    task_count: int = 0
    max_tasks: int = 1
    workspace_id: str = ""
    current_video_id: str | None = None
    current_title: str = ""
    current_content_type: str = ""
    current_external_id: str = ""
    current_phase: str = ""


class AgentStatusManager:
    def __init__(
        self,
        event_bus: EventBus | None = None,
        discover_agents: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._discover_agents = discover_agents
        self.agents: list[AgentStatus] = []
        self._clients: set[WebSocket] = set()
        self._forward_tasks: dict[WebSocket, asyncio.Task] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._busy_video_ids: set[str] = set()
        self._agent_video_ids: dict[tuple[str, str], list[str]] = {}
        self._workspace_assignments: dict[str, dict[str, int]] = {}
        self._lock = threading.Lock()
        self._broadcast_controller = AgentBroadcastController(self._lock, lambda: self._broadcast())

    @property
    def broadcast_controller(self) -> AgentBroadcastController:
        return self._broadcast_controller

    def discover(self) -> list[AgentStatus]:
        try:
            records = self._discover_agents() if self._discover_agents is not None else []
            agents = [
                AgentStatus(
                    id=r["id"],
                    name=r.get("identityName") or r["id"],
                    busy=False,
                )
                for r in records
            ]
        except Exception:
            agents = []
        with self._lock:
            self.agents = agents
        return list(agents)

    def set_workspace_assignment(
        self, workspace_id: str, agent_id: str, concurrency_limit: int = 1
    ) -> None:
        with self._lock:
            self._workspace_assignments.setdefault(workspace_id, {})[agent_id] = concurrency_limit

    def remove_workspace_assignment(self, workspace_id: str, agent_id: str) -> None:
        with self._lock:
            self._workspace_assignments.get(workspace_id, {}).pop(agent_id, None)

    def get_allowed_agents(self, workspace_id: str) -> list[str] | None:
        with self._lock:
            if workspace_id not in self._workspace_assignments:
                return None if workspace_id == "video-hive" else []
            return list(self._workspace_assignments[workspace_id].keys())

    def is_agent_allowed(self, workspace_id: str, agent_id: str) -> bool:
        with self._lock:
            if workspace_id not in self._workspace_assignments:
                return workspace_id == "video-hive"
            return agent_id in self._workspace_assignments[workspace_id]

    def get_all(self) -> list[AgentStatus]:
        with self._lock:
            return list(self.agents)

    def add_pi_agent_for_workspace(self, workspace_id: str, max_tasks: int = 1) -> None:
        should_broadcast = False
        with self._lock:
            for agent in self.agents:
                if agent.id == "pi" and agent.workspace_id == workspace_id:
                    if agent.max_tasks != max_tasks:
                        agent.max_tasks = max_tasks
                        should_broadcast = True
                    break
            else:
                self.agents.append(
                    AgentStatus(
                        id="pi",
                        name="Pi Agent",
                        busy=False,
                        task_count=0,
                        max_tasks=max_tasks,
                        workspace_id=workspace_id,
                    )
                )
                should_broadcast = True
        if should_broadcast:
            self._broadcast()

    def remove_pi_agent_for_workspace(self, workspace_id: str) -> None:
        with self._lock:
            before = len(self.agents)
            self.agents = [
                agent
                for agent in self.agents
                if not (agent.id == "pi" and agent.workspace_id == workspace_id)
            ]
            removed = len(self.agents) != before
        if removed:
            self._broadcast()

    def set_busy(
        self, agent_id: str, video: str | dict[str, Any], *, workspace_id: str = ""
    ) -> None:
        video_id = video if isinstance(video, str) else str(video.get("id", ""))
        with self._lock:
            if video_id:
                self._busy_video_ids.add(video_id)
                key = (agent_id, workspace_id)
                self._agent_video_ids.setdefault(key, []).append(video_id)
            for agent in self.agents:
                if agent.id == agent_id and agent.workspace_id == workspace_id:
                    agent.task_count += 1
                    agent.busy = True
                    agent.current_video_id = video_id
                    if isinstance(video, dict):
                        agent.current_title = str(video.get("title", ""))
                        agent.current_content_type = str(video.get("content_type", ""))
                        agent.current_external_id = str(video.get("external_id", ""))
                        agent.current_phase = str(video.get("current_phase", ""))
                    break
        self._broadcast_controller.mark_broadcast_pending()

    def set_idle(self, agent_id: str, *, workspace_id: str = "") -> None:
        with self._lock:
            key = (agent_id, workspace_id)
            video_ids = self._agent_video_ids.get(key, [])
            video_id = video_ids.pop() if video_ids else ""
            if video_ids:
                self._agent_video_ids[key] = video_ids
            else:
                self._agent_video_ids.pop(key, None)
            if video_id:
                self._busy_video_ids.discard(video_id)
            for agent in self.agents:
                if agent.id == agent_id and agent.workspace_id == workspace_id:
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
        self._broadcast_controller.mark_broadcast_pending()

    def is_video_busy(self, video_id: str) -> bool:
        with self._lock:
            return video_id in self._busy_video_ids

    def has_pending_broadcast(self) -> bool:
        return self._broadcast_controller.has_pending_broadcast()

    def flush_pending_broadcast(self) -> None:
        self._broadcast_controller.flush_pending_broadcast()

    def to_dicts(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "id": a.id,
                    "name": a.name,
                    "busy": a.busy,
                    "task_count": a.task_count,
                    "max_tasks": a.max_tasks,
                    "workspace_id": a.workspace_id,
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
        if self._event_bus is None:
            return
        bus = self._event_bus
        queue = bus.subscribe("agents")

        async def _forward() -> None:
            try:
                while True:
                    data = await queue.get()
                    if data is _EVICTED:
                        return
                    await websocket.send_text(data)
            finally:
                bus.unsubscribe("agents", queue)

        self._forward_tasks[websocket] = asyncio.create_task(_forward())

    def disconnect(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)
        task = self._forward_tasks.pop(websocket, None)
        if task is not None:
            task.cancel()

    def _broadcast(self) -> None:
        payload = self.to_dicts()
        if self._event_bus is not None:
            self._event_bus.publish("agents", json.dumps(payload))
            return
        # 无 bus 回退（测试直构造路径）：保持原 run_coroutine_threadsafe 直发
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        for ws in list(self._clients):
            try:
                asyncio.run_coroutine_threadsafe(ws.send_json(payload), loop)
            except Exception:
                self._clients.discard(ws)
