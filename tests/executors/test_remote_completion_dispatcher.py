from __future__ import annotations

import threading

from server.app.executors._remote_completion_dispatcher import CompletionDispatcher
from server.app.executors.remote_broker import RemoteOutcome


def _outcome() -> RemoteOutcome:
    return RemoteOutcome(status="completed", exit_code=0)


def test_dispatch_runs_callbacks_on_dispatcher_thread() -> None:
    dispatcher = CompletionDispatcher()
    try:
        seen: list[tuple[str, str]] = []

        def callback(execution_id: str, outcome: RemoteOutcome) -> None:
            seen.append((execution_id, threading.current_thread().name))

        dispatcher.dispatch(callback, "e1", _outcome())
        dispatcher.wait_idle()
        assert seen == [("e1", "remote-completion-dispatcher")]
    finally:
        dispatcher.close()


def test_callbacks_run_in_order_and_failures_are_isolated() -> None:
    dispatcher = CompletionDispatcher()
    try:
        calls: list[str] = []

        def boom(execution_id: str, outcome: RemoteOutcome) -> None:
            calls.append(f"boom:{execution_id}")
            raise RuntimeError("boom")

        def ok(execution_id: str, outcome: RemoteOutcome) -> None:
            calls.append(f"ok:{execution_id}")

        dispatcher.dispatch(boom, "e1", _outcome())
        dispatcher.dispatch(ok, "e1", _outcome())
        dispatcher.dispatch(ok, "e2", _outcome())
        dispatcher.wait_idle()
        assert calls == ["boom:e1", "ok:e1", "ok:e2"]
    finally:
        dispatcher.close()
