import asyncio
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket

from server.app.agent_broadcast import AgentBroadcastController
from server.app.event_bus import _EVICTED, EventBus

logger = logging.getLogger(__name__)


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


def _agent_dict(agent: AgentStatus) -> dict[str, Any]:
    return {
        "id": agent.id,
        "name": agent.name,
        "busy": agent.busy,
        "task_count": agent.task_count,
        "max_tasks": agent.max_tasks,
        "workspace_id": agent.workspace_id,
        "current_video_id": agent.current_video_id,
        "current_title": agent.current_title,
        "current_content_type": agent.current_content_type,
        "current_external_id": agent.current_external_id,
        "current_phase": agent.current_phase,
    }


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
        # Incremental WS events buffered between broadcast flushes, keyed by
        # (agent_id, workspace_id) so the latest state per agent wins.
        self._pending_events: dict[tuple[str, str], dict[str, Any]] = {}
        self._snapshot_pending = False
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

    def get_all(self) -> list[AgentStatus]:
        with self._lock:
            return list(self.agents)

    def ensure_workspace_agent(
        self, agent_id: str, workspace_id: str, *, max_tasks: int = 1, name: str = ""
    ) -> None:
        """Idempotently upsert a workspace-scoped status row for the panel.

        Distributed executions (broker claims by remote Agent Workers) have no
        in-process runner to discover, so the broker registers one row per
        (Worker, workspace) here; ``max_tasks`` mirrors the Worker's machine
        capacity so the panel shows "name busy/capacity" per Worker.
        """
        should_broadcast = False
        with self._lock:
            for agent in self.agents:
                if agent.id == agent_id and agent.workspace_id == workspace_id:
                    if agent.max_tasks != max_tasks:
                        agent.max_tasks = max_tasks
                        should_broadcast = True
                    break
            else:
                self.agents.append(
                    AgentStatus(
                        id=agent_id,
                        name=name or agent_id,
                        busy=False,
                        task_count=0,
                        max_tasks=max_tasks,
                        workspace_id=workspace_id,
                    )
                )
                should_broadcast = True
        if should_broadcast:
            self._broadcast_snapshot()

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
            self._broadcast_snapshot()

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
            self._broadcast_snapshot()

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
                    self._pending_events[(agent.id, agent.workspace_id)] = {
                        "type": "agent_busy",
                        "agent": _agent_dict(agent),
                    }
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
                    self._pending_events[(agent.id, agent.workspace_id)] = {
                        "type": "agent_idle",
                        "agent": _agent_dict(agent),
                    }
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
            return [_agent_dict(a) for a in self.agents]

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)
        self._loop = asyncio.get_running_loop()
        await websocket.send_json({"type": "snapshot", "agents": self.to_dicts()})
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
        """Flush buffered events: envelopes per changed agent, snapshot otherwise.

        A pending structural change (or an empty buffer, e.g. a set_busy on an
        unknown agent) supersedes incrementals with a full snapshot envelope.
        """
        with self._lock:
            snapshot = self._snapshot_pending
            self._snapshot_pending = False
            events = [] if snapshot else list(self._pending_events.values())
            self._pending_events.clear()
        if not events:
            self._send_envelope({"type": "snapshot", "agents": self.to_dicts()})
            return
        for event in events:
            self._send_envelope(event)

    def _broadcast_snapshot(self) -> None:
        with self._lock:
            self._snapshot_pending = True
        self._broadcast()

    def _send_envelope(self, envelope: dict[str, Any]) -> None:
        if self._event_bus is not None:
            self._event_bus.publish("agents", json.dumps(envelope))
            return
        # 无 bus 回退（测试直构造路径）：保持原 run_coroutine_threadsafe 直发
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        for ws in list(self._clients):
            try:
                asyncio.run_coroutine_threadsafe(ws.send_json(envelope), loop)
            except Exception:
                logger.warning("agent WS send failed, dropping client", exc_info=True)
                self._clients.discard(ws)
