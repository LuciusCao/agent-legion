from __future__ import annotations

import logging
from contextlib import contextmanager

from server.app.agent_broker.dispatch_pool import AgentEnqueuePool


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

    with caplog.at_level(logging.ERROR, logger="server.app.agent_broker.dispatch_pool"):
        pool._run()

    assert calls == ["recovered"]
    assert "background agent enqueue failed" in caplog.text
    assert "bundle failed" in caplog.text


def test_run_survives_successive_failures_across_items(caplog) -> None:
    """#204 保留补强：连续多个失败项不杀死线程循环——逐项隔离，
    每个失败各自记 exception 日志，后续项继续执行。"""
    pool = AgentEnqueuePool(workers=0, max_pending=4)
    calls: list[str] = []

    def fail(name: str) -> None:
        def _inner() -> None:
            raise RuntimeError(name)

        return _inner

    pool._queue.put_nowait(fail("first"))
    pool._queue.put_nowait(fail("second"))
    pool._queue.put_nowait(lambda: calls.append("recovered"))
    pool._queue.put_nowait(None)

    with caplog.at_level(logging.ERROR, logger="server.app.agent_broker.dispatch_pool"):
        pool._run()

    assert calls == ["recovered"]
    assert caplog.text.count("background agent enqueue failed") == 2


def test_empty_claim_trigger_survives_callback_failure(caplog, monkeypatch) -> None:
    """#204 保留补强：restock 回调抛错 → exception 日志、不向 claim 响应路径
    上抛（inline 调用方是 Worker claim 路径）。"""
    from server.app.agent_broker.empty import EmptyClaimTrigger

    trigger = EmptyClaimTrigger(debounce_seconds=0.0)
    calls: list[int] = []

    def _boom() -> None:
        calls.append(1)
        raise RuntimeError("restock exploded")

    trigger.on_empty_queue = _boom

    class _Conn:
        def execute(self, *_args, **_kwargs):
            class _Result:
                def fetchone(self):
                    return None  # 队列真空 → 走到回调

            return _Result()

    @contextmanager
    def _fake_read_connection(_dsn):
        yield _Conn()

    monkeypatch.setattr("server.app.agent_broker.empty.read_connection", _fake_read_connection)

    with caplog.at_level(logging.ERROR, logger="server.app.agent_broker.empty"):
        trigger.note_empty_claim(None)  # type: ignore[arg-type]

    assert calls == [1]
    assert "empty-queue restock callback failed" in caplog.text
    assert "restock exploded" in caplog.text
