"""Agent status facade: assembles registry, workload, and WS broadcasting.

Split (issue #2 phase 2): ``agent_registry`` owns the row set (discovery +
broker upserts), ``agent_workload`` owns busy/idle transitions and the
incremental event buffer, ``agent_broadcast`` owns flush timing. This module
keeps the WS serving (connect/disconnect/envelope sending) and the public
``AgentStatusManager`` API every consumer (main, routes, broker, tests)
already depends on — the facade is the only place that knows all three parts.
"""

import asyncio
import json
import logging
import threading
from collections.abc import Callable
from typing import Any

from fastapi import WebSocket

from server.app.events.agent_broadcast import AgentBroadcastController
from server.app.events.agent_registry import AgentRegistry, AgentStatus
from server.app.events.agent_workload import AgentWorkload
from server.app.events.bus import _EVICTED, EventBus

logger = logging.getLogger(__name__)

__all__ = ["AgentStatus", "AgentStatusManager"]


class AgentStatusManager:
    def __init__(
        self,
        event_bus: EventBus | None = None,
        discover_agents: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._lock = threading.Lock()
        self._registry = AgentRegistry(self._lock, discover_agents)
        self._workload = AgentWorkload(self._lock, self._registry)
        self._clients: set[WebSocket] = set()
        self._forward_tasks: dict[WebSocket, asyncio.Task] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._broadcast_controller = AgentBroadcastController(self._lock, lambda: self._broadcast())

    @property
    def broadcast_controller(self) -> AgentBroadcastController:
        return self._broadcast_controller

    # -- registry (row set) -------------------------------------------------

    @property
    def agents(self) -> list[AgentStatus]:
        """Direct row access for tests and legacy monkeypatching; prefer get_all()."""
        return self._registry.agents

    @agents.setter
    def agents(self, value: list[AgentStatus]) -> None:
        self._registry.agents = value

    def discover(self) -> list[AgentStatus]:
        return self._registry.discover()

    def get_all(self) -> list[AgentStatus]:
        return self._registry.get_all()

    def to_dicts(self) -> list[dict[str, Any]]:
        return self._registry.to_dicts()

    def ensure_workspace_agent(
        self, agent_id: str, workspace_id: str, *, max_tasks: int = 1, name: str = ""
    ) -> None:
        if self._registry.ensure_workspace_agent(
            agent_id, workspace_id, max_tasks=max_tasks, name=name
        ):
            self._broadcast_snapshot()

    # -- workload (busy/idle transitions) ------------------------------------

    def set_busy(self, agent_id: str, *, workspace_id: str = "") -> None:
        self._workload.set_busy(agent_id, workspace_id=workspace_id)
        self._broadcast_controller.mark_broadcast_pending()

    def set_idle(self, agent_id: str, *, workspace_id: str = "") -> None:
        self._workload.set_idle(agent_id, workspace_id=workspace_id)
        self._broadcast_controller.mark_broadcast_pending()

    # -- broadcasting ---------------------------------------------------------

    def has_pending_broadcast(self) -> bool:
        return self._broadcast_controller.has_pending_broadcast()

    def flush_pending_broadcast(self) -> None:
        self._broadcast_controller.flush_pending_broadcast()

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
        events = self._workload.take_flush_batch()
        if not events:
            self._send_envelope({"type": "snapshot", "agents": self.to_dicts()})
            return
        for event in events:
            self._send_envelope(event)

    def _broadcast_snapshot(self) -> None:
        self._workload.mark_snapshot_pending()
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
                # #204 broad-except audit: per-client containment on the
                # no-bus fallback path (test/direct-construction only in
                # production code the bus handles fan-out). run_coroutine_
                # threadsafe raises when the submit races loop shutdown
                # (RuntimeError) or the websocket object rejects the
                # coroutine; either way one dead client must not abort the
                # envelope delivery to the remaining clients in this loop.
                # Discarding the client mirrors disconnect(); the warning
                # with traceback is the signal — nothing is masked because
                # broadcasting is fire-and-forget by design.
                logger.warning("agent WS send failed, dropping client", exc_info=True)
                self._clients.discard(ws)
