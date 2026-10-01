import asyncio
import contextlib

from server.app.job_intake_background import consume_intake_batches


class FlakyQueue:
    """consume_once runs via asyncio.to_thread (a worker thread), so the
    second-call signal crosses back into the loop with
    loop.call_soon_threadsafe — plain asyncio.Event.set from the worker
    thread would race the loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.calls = 0
        self.second_call = asyncio.Event()
        self._loop = loop

    def consume_once(self) -> bool:
        self.calls += 1
        if self.calls >= 2:
            self._loop.call_soon_threadsafe(self.second_call.set)
        if self.calls == 1:
            raise RuntimeError("transient db failure")
        return False


def test_consume_intake_batches_survives_transient_failure():
    """Regression: one exception from consume_once (e.g. a DB hiccup while
    claiming) must not kill the intake consumer task forever.

    Event-driven, not wall-clock: the test waits on the second consume_once
    call happening (5s upper bound only bounds the BROKEN case — if the
    consumer dies on the transient failure, the event never fires and the
    wait times out). A sleep-based wait could outrun the loop under CI
    load and fail spuriously."""

    async def _run() -> int:
        queue = FlakyQueue(asyncio.get_running_loop())
        task = asyncio.create_task(consume_intake_batches(queue, failure_backoff_seconds=0.01))
        try:
            await asyncio.wait_for(queue.second_call.wait(), timeout=5)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return queue.calls

    calls = asyncio.run(_run())

    assert calls >= 2  # 失败后继续轮询，而不是任务悄悄退出
