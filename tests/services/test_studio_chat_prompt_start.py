"""Prompt-start and accept-time cancellation interleavings (#1153 A2 r3)."""

from __future__ import annotations

import asyncio
import contextlib
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from server.app.studio_chat import admission_turn as direct_module
from server.app.studio_chat import inbound_delivery as queued_module
from server.app.studio_chat.acp_session import _CLOSE
from server.app.studio_chat.background_wakeup import wake_session
from server.app.studio_chat.callbacks import ServiceCallbacks
from server.app.studio_chat.process_exit import AcpConnectionLost
from server.app.studio_chat.timing import StudioTimingConfig
from tests.helpers import studio_chat_fixtures as fixtures
from tests.helpers import wait_for_predicate

admission = fixtures.admission


def _wait(condition, timeout: float = 5.0) -> None:
    wait_for_predicate(condition, timeout=timeout, interval=0.02)


class _FakeProcess:
    def __init__(self) -> None:
        self.returncode = None
        self.kill_calls = 0
        self.killed = threading.Event()

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9
        self.killed.set()


def _install(service, sid: str, runtime, *, cancel_grace: float = 0.05) -> _FakeProcess:
    process = _FakeProcess()
    runtime.handle.timing = StudioTimingConfig(
        prompt_timeout_seconds=30, cancel_grace_seconds=cancel_grace, close_grace_seconds=0.05
    )
    runtime.handle._process = process
    callbacks = ServiceCallbacks(service, sid)
    callbacks.runtime = runtime
    runtime.handle.callbacks = callbacks
    return process


def _events(service, sid: str, workspace: str, name: str) -> list[dict]:
    return [
        message["content"]
        for message in service.list_messages(sid, workspace)
        if message["kind"] == "status" and message["content"].get("event") == name
    ]


@pytest.mark.parametrize("queued", [False, True], ids=["direct", "queued-delivery"])
def test_allowed_then_fatal_never_creates_new_rpc(admission, monkeypatch, queued) -> None:
    service, _db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime)
    service.send_message(sid, workspace, "start the gate turn")
    watchdog = runtime.turn_watchdog
    monkeypatch.setattr(watchdog, "_escalate", Mock())
    gate = runtime.handle._prompt_start_gate
    original_allowed = gate._allowed
    injected = False

    def allowed_after_fatal() -> bool:
        nonlocal injected
        allowed = original_allowed()
        if not injected:
            injected = True
            watchdog._deadline = 0
            watchdog._fire(watchdog._generation, watchdog._reason, watchdog._cancel_epoch)
        return allowed

    monkeypatch.setattr(gate, "_allowed", allowed_after_fatal)
    before_start = Mock(return_value=True)
    if queued:
        runtime.handle._queue.get_nowait()
        runtime.handle._queue.put(("queued text", before_start))
    runtime.handle._queue.put(_CLOSE)
    conn = SimpleNamespace(
        prompt=AsyncMock(return_value=SimpleNamespace(stop_reason="end_turn")),
        cancel=AsyncMock(),
    )

    async def consume() -> None:
        death = asyncio.create_task(asyncio.Event().wait())
        try:
            await asyncio.wait_for(runtime.handle._prompt_loop(conn, "acp-session", death), 2)
        except AcpConnectionLost:
            pass
        finally:
            death.cancel()
            await asyncio.gather(death, return_exceptions=True)

    asyncio.run(consume())

    assert process.kill_calls == 1
    assert conn.prompt.await_count == 0
    assert before_start.call_count == (1 if queued else 0)
    watchdog.cleanup_thread.join(timeout=2)


def test_prompt_creation_wedged_does_not_delay_kill(admission, monkeypatch) -> None:
    service, _db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime)
    service.send_message(sid, workspace, "wedge inside prompt creation")
    watchdog = runtime.turn_watchdog
    monkeypatch.setattr(watchdog, "_escalate", Mock())
    entered, release = threading.Event(), threading.Event()
    runtime.handle._queue.put(_CLOSE)

    def prompt(*_args):
        entered.set()
        assert release.wait(timeout=5)
        return asyncio.sleep(0, result=SimpleNamespace(stop_reason="end_turn"))

    conn = SimpleNamespace(prompt=prompt, cancel=AsyncMock())

    async def consume() -> None:
        with contextlib.suppress(AcpConnectionLost):
            await runtime.handle._prompt_loop(conn, "acp-session")

    loop_thread = threading.Thread(target=lambda: asyncio.run(consume()), daemon=True)
    loop_thread.start()
    assert entered.wait(timeout=2)
    watchdog._deadline = 0
    watchdog._fire(watchdog._generation, watchdog._reason, watchdog._cancel_epoch)

    assert process.kill_calls == 1
    assert runtime.handle._prompt_start_gate.mutex.locked()
    assert loop_thread.is_alive()
    release.set()
    loop_thread.join(timeout=2)
    watchdog.cleanup_thread.join(timeout=2)
    assert not loop_thread.is_alive()


@pytest.mark.parametrize("queued", [False, True], ids=["direct-accept", "queued-delivery"])
def test_accept_time_cancel_binds_to_exact_turn_and_kills(admission, monkeypatch, queued) -> None:
    service, db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime, cancel_grace=5)
    accepted, release_accept = threading.Event(), threading.Event()
    target_module = queued_module if queued else direct_module
    real_open_turn = target_module.open_turn

    def paused_open_turn(*args, **kwargs):
        accepted.set()
        assert release_accept.wait(timeout=5)
        return real_open_turn(*args, **kwargs)

    monkeypatch.setattr(target_module, "open_turn", paused_open_turn)
    message = None
    if queued:
        assert wake_session(service, sid, runtime, ["finished-task"])
        message = service.send_message(sid, workspace, "queued cancel binding")
        _background_prompt, queued_prompt = (
            runtime.handle._queue.get_nowait(),
            runtime.handle._queue.get_nowait(),
        )
        service._events().on_turn_end(sid, "end_turn")
        _prompt_text, before_start = queued_prompt
        worker = threading.Thread(target=before_start, daemon=True)
    else:
        worker = threading.Thread(
            target=lambda: service.send_message(sid, workspace, "direct cancel binding"),
            daemon=True,
        )
    worker.start()
    assert accepted.wait(timeout=2)
    assert db.get_studio_chat_session(sid)["status"] == "running"
    assert runtime.turn_watchdog is None or runtime.turn_watchdog._disarmed
    assert runtime.turn_admission is not None

    cancel_thread = threading.Thread(target=service.cancel, args=(sid, workspace), daemon=True)
    cancel_thread.start()
    _wait(lambda: getattr(runtime, "pending_cancel", None) is not None, timeout=2)
    release_accept.set()
    worker.join(timeout=2)
    cancel_thread.join(timeout=2)
    watchdog = runtime.turn_watchdog
    first_deadline = watchdog._deadline
    assert watchdog._cancel_epoch == 1
    assert first_deadline is not None

    service.cancel(sid, workspace)
    assert watchdog._cancel_epoch == 2
    assert watchdog._deadline == first_deadline
    watchdog._deadline = 0
    watchdog._fire(watchdog._generation, watchdog._reason, watchdog._cancel_epoch)
    watchdog.cleanup_thread.join(timeout=2)

    _wait(
        lambda: (
            service.runtime(sid) is None and db.get_studio_chat_session(sid)["status"] == "error"
        )
    )
    assert process.kill_calls == 1
    if message is None:
        message = db.list_studio_chat_messages(sid)[0]
    unknown = [
        event
        for event in _events(service, sid, workspace, "error")
        if event.get("message_id") == message["id"] and event.get("outcome") == "unknown"
    ]
    assert len(unknown) == 1
    assert len(_events(service, sid, workspace, "cancel_requested")) == 2


def test_cancel_without_accepted_turn_does_not_poison_next_prompt(admission) -> None:
    service, _db, sid, workspace, runtime = admission
    _process = _install(service, sid, runtime)

    service.cancel(sid, workspace)
    assert getattr(runtime, "pending_cancel", None) is None

    service.send_message(sid, workspace, "ordinary next turn")
    assert runtime.turn_watchdog._cancel_epoch == 0
    assert getattr(runtime, "pending_cancel", None) is None
