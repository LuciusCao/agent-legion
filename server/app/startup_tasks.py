from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

    from server.app.agent_broadcast import AgentBroadcastController
    from server.app.job_events import WorkspaceJobEventAggregator
    from server.app.services.ops_metrics import OpsMetricsService

from server.app.job_intake_background import consume_intake_batches
from server.app.ops_metrics_background import run_ops_metrics_loop


class BackgroundTasks:
    def __init__(
        self,
        workspace_event_aggregator: WorkspaceJobEventAggregator,
        agent_broadcast_controller: AgentBroadcastController,
        job_intake_queue: Any,
        ops_metrics: OpsMetricsService | None = None,
    ) -> None:
        self._workspace_event_aggregator = workspace_event_aggregator
        self._agent_broadcast_controller = agent_broadcast_controller
        self._job_intake_queue = job_intake_queue
        self._ops_metrics = ops_metrics

    def start(self, app: FastAPI) -> None:
        app.state.workspace_event_aggregator_task = asyncio.create_task(
            self._workspace_event_aggregator.run(interval_seconds=0.5)
        )

        async def _flush_agent_status_loop() -> None:
            while True:
                self._agent_broadcast_controller.flush_pending_broadcast()
                await asyncio.sleep(0.5)

        app.state.agent_status_flush_task = asyncio.create_task(_flush_agent_status_loop())

        app.state.job_intake_queue_task = asyncio.create_task(
            consume_intake_batches(self._job_intake_queue)
        )

        if self._ops_metrics is not None:
            app.state.ops_metrics_task = asyncio.create_task(
                run_ops_metrics_loop(self._ops_metrics)
            )

    async def stop(self, app: FastAPI, timeout_seconds: float = 5.0) -> None:
        tasks: list[asyncio.Task] = []
        for attr in (
            "workspace_event_aggregator_task",
            "agent_status_flush_task",
            "job_intake_queue_task",
            "ops_metrics_task",
        ):
            task = getattr(app.state, attr, None)
            if task is not None:
                task.cancel()
                tasks.append(task)
        if tasks:
            # Wait for cancellation to settle before lifespan teardown closes
            # database pools; in-flight thread work (asyncio.to_thread) is not
            # interruptible, so bound the wait and let the timeout win.
            await asyncio.wait(tasks, timeout=timeout_seconds)
