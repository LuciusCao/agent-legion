import asyncio
from types import SimpleNamespace

from server.app.startup_tasks import BackgroundTasks


def test_stop_cancels_and_awaits_background_tasks():
    """stop() must wait for cancelled tasks to settle so lifespan teardown
    (database pool close) does not race in-flight background work."""
    background = BackgroundTasks(
        workspace_event_aggregator=object(),
        agent_broadcast_controller=object(),
        job_intake_queue=object(),
    )
    settled: list[str] = []

    async def _loop(name: str) -> None:
        try:
            while True:
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            settled.append(name)
            raise

    async def _run() -> None:
        app = SimpleNamespace(
            state=SimpleNamespace(
                workspace_event_aggregator_task=asyncio.create_task(_loop("aggregator")),
                agent_status_flush_task=asyncio.create_task(_loop("agent_flush")),
                job_intake_queue_task=asyncio.create_task(_loop("intake")),
            )
        )
        await asyncio.sleep(0.05)  # 让任务先跑起来再取消
        await background.stop(app, timeout_seconds=2.0)
        assert app.state.workspace_event_aggregator_task.cancelled()
        assert app.state.agent_status_flush_task.cancelled()
        assert app.state.job_intake_queue_task.cancelled()

    asyncio.run(_run())

    assert sorted(settled) == ["agent_flush", "aggregator", "intake"]
