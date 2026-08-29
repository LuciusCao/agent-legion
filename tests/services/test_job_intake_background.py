import asyncio
import contextlib

from server.app.job_intake_background import consume_intake_batches


class FlakyQueue:
    def __init__(self) -> None:
        self.calls = 0

    def consume_once(self) -> bool:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient db failure")
        return False


def test_consume_intake_batches_survives_transient_failure():
    """Regression: one exception from consume_once (e.g. a DB hiccup while
    claiming) must not kill the intake consumer task forever."""
    queue = FlakyQueue()

    async def _run() -> None:
        task = asyncio.create_task(consume_intake_batches(queue, failure_backoff_seconds=0.01))
        await asyncio.sleep(0.2)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(_run())

    assert queue.calls >= 2  # 失败后继续轮询，而不是任务悄悄退出
