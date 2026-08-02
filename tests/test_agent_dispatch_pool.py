from __future__ import annotations

import logging

from server.app.agent_dispatch_pool import AgentEnqueuePool


def test_submit_returns_false_when_the_pending_queue_is_full() -> None:
    pool = AgentEnqueuePool(workers=0, max_pending=1)

    assert pool.submit(lambda: None) is True
    assert pool.submit(lambda: None) is False


def test_run_executes_pending_work_until_the_stop_sentinel() -> None:
    pool = AgentEnqueuePool(workers=0, max_pending=2)
    calls: list[str] = []
    pool._queue.put_nowait(lambda: calls.append("ran"))
    pool._queue.put_nowait(None)

    pool._run()

    assert calls == ["ran"]


def test_run_logs_a_failure_and_continues_to_the_next_item(caplog) -> None:
    pool = AgentEnqueuePool(workers=0, max_pending=3)
    calls: list[str] = []

    def fail() -> None:
        raise RuntimeError("bundle failed")

    pool._queue.put_nowait(fail)
    pool._queue.put_nowait(lambda: calls.append("recovered"))
    pool._queue.put_nowait(None)

    with caplog.at_level(logging.ERROR, logger="server.app.agent_dispatch_pool"):
        pool._run()

    assert calls == ["recovered"]
    assert "background agent enqueue failed" in caplog.text
    assert "bundle failed" in caplog.text
