from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

    from server.app.agent_broadcast import AgentBroadcastController
    from server.app.job_events import WorkspaceJobEventAggregator


class BackgroundTasks:
    """Manages background tasks created during app startup."""

    def __init__(
        self,
        workspace_event_aggregator: WorkspaceJobEventAggregator,
        agent_broadcast_controller: AgentBroadcastController,
    ) -> None:
        self._workspace_event_aggregator = workspace_event_aggregator
        self._agent_broadcast_controller = agent_broadcast_controller

    def start(self, app: FastAPI) -> None:
        app.state.workspace_event_aggregator_task = asyncio.create_task(
            self._workspace_event_aggregator.run(interval_seconds=0.5)
        )

        async def _flush_agent_status_loop() -> None:
            while True:
                self._agent_broadcast_controller.flush_pending_broadcast()
                await asyncio.sleep(0.5)

        app.state.agent_status_flush_task = asyncio.create_task(_flush_agent_status_loop())

    def stop(self, app: FastAPI) -> None:
        for attr in ("workspace_event_aggregator_task", "agent_status_flush_task"):
            task = getattr(app.state, attr, None)
            if task is not None:
                task.cancel()
