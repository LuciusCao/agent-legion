"""Queued baseline failure must settle or retain controlled FIFO ownership."""

from __future__ import annotations

import asyncio
import contextlib
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from server.app.studio_chat.acp_session import _CLOSE
from server.app.studio_chat.background_wakeup import wake_session
from server.app.studio_chat.callbacks import ServiceCallbacks
from server.app.studio_chat.process_exit import AcpConnectionLost
from server.app.studio_chat.restart_settlement import reconcile_zombie_session
from server.app.studio_chat.runtime import SessionRuntime
from server.app.studio_chat.timing import StudioTimingConfig
from tests.helpers import studio_chat_fixtures as fixtures
from tests.helpers import wait_for_predicate

admission = fixtures.admission


def _wait(condition, timeout: float = 3.0) -> None:
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


def _install(service, sid: str, runtime, *, cancel_grace: float = 5.0) -> _FakeProcess:
    process = _FakeProcess()
    runtime.handle.timing = StudioTimingConfig(
        prompt_timeout_seconds=30,
        cancel_grace_seconds=cancel_grace,
        close_grace_seconds=0.05,
    )
    runtime.handle._process = process
    callbacks = ServiceCallbacks(service, sid)
    callbacks.runtime = runtime
    runtime.handle.callbacks = callbacks
    return process


def _events(db, sid: str, name: str) -> list[dict]:
    return [
        message["content"]
        for message in db.list_studio_chat_messages(sid)
        if message["kind"] == "status" and message["content"].get("event") == name
    ]


def _unknowns(db, sid: str, message_id: str) -> list[dict]:
    return [
        event
        for event in _events(db, sid, "error")
        if event.get("message_id") == message_id and event.get("outcome") == "unknown"
    ]


def _dropped(db, sid: str, message_id: str) -> list[dict]:
    return [
        event
        for event in _events(db, sid, "queued_dropped")
        if event.get("message_id") == message_id
    ]


def _prepare_two(service, sid: str, workspace: str, runtime):
    assert wake_session(service, sid, runtime, ["finished-task"])
    first = service.send_message(sid, workspace, "first queued baseline")
    second = service.send_message(sid, workspace, "second queued baseline")
    envelopes = [runtime.handle._queue.get_nowait() for _ in range(3)]
    service._events().on_turn_end(sid, "end_turn")
    if runtime.empty_turn_timer is not None:
        runtime.empty_turn_timer.cancel()
    return first, second, envelopes[1:]


def _consume(runtime, conn) -> None:
    with contextlib.suppress(AcpConnectionLost):
        asyncio.run(runtime.handle._prompt_loop(conn, "acp-session"))


def test_transient_baseline_failure_visibly_drops_before_newer_prompt(
    admission, monkeypatch
) -> None:
    service, db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime)
    first, second, envelopes = _prepare_two(service, sid, workspace, runtime)
    runtime.handle._queue.put(envelopes[0])
    runtime.handle._queue.put(envelopes[1])
    runtime.handle._queue.put(_CLOSE)
    real_list = db.list_studio_chat_messages
    failures = []

    def fail_first_baseline(*args, **kwargs):
        if kwargs.get("limit") == 1 and not failures:
            failures.append(1)
            raise RuntimeError("transient baseline read failure")
        return real_list(*args, **kwargs)

    monkeypatch.setattr(db, "list_studio_chat_messages", fail_first_baseline)
    conn = SimpleNamespace(
        prompt=AsyncMock(return_value=SimpleNamespace(stop_reason="end_turn")), cancel=AsyncMock()
    )
    _consume(runtime, conn)

    assert failures == [1]
    assert len(_dropped(db, sid, first["id"])) == 1
    assert _dropped(db, sid, second["id"]) == []
    assert len(_unknowns(db, sid, first["id"])) == 0
    assert conn.prompt.await_count == 1
    prompt_blocks = conn.prompt.await_args.args[1]
    assert [block.text for block in prompt_blocks] == [envelopes[1].text]
    delivered = [
        event
        for event in _events(db, sid, "queued_delivered")
        if event.get("message_id") == second["id"]
    ]
    completed = [
        event for event in _events(db, sid, "turn_end") if event.get("message_id") == second["id"]
    ]
    assert len(delivered) == len(completed) == 1
    assert runtime.inbound_pending == 0
    assert first["id"] not in runtime.inbound_messages
    assert second["id"] not in runtime.inbound_messages
    assert db.get_studio_chat_session(sid)["status"] == "idle"
    assert process.kill_calls == 0


def test_unreadable_baseline_and_drop_write_fail_closed_to_one_m19_drop(
    admission, monkeypatch
) -> None:
    service, db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime)
    assert wake_session(service, sid, runtime, ["finished-task"])
    message = service.send_message(sid, workspace, "persistent baseline failure")
    runtime.handle._queue.get_nowait()
    envelope = runtime.handle._queue.get_nowait()
    service._events().on_turn_end(sid, "end_turn")
    if runtime.empty_turn_timer is not None:
        runtime.empty_turn_timer.cancel()
    runtime.handle._queue.put(envelope)
    runtime.handle._queue.put(_CLOSE)

    real_list = db.list_studio_chat_messages

    def unreadable(*args, **kwargs):
        if kwargs.get("limit") == 1:
            raise RuntimeError("persistent baseline read failure")
        return real_list(*args, **kwargs)

    real_append = db.append_studio_chat_message

    def drop_write_failure(session_id, kind, role, content):
        if content.get("event") == "queued_dropped":
            raise RuntimeError("queued drop write failure")
        return real_append(session_id, kind, role, content)

    monkeypatch.setattr(db, "list_studio_chat_messages", unreadable)
    monkeypatch.setattr(db, "append_studio_chat_message", drop_write_failure)
    conn = SimpleNamespace(
        prompt=AsyncMock(return_value=SimpleNamespace(stop_reason="end_turn")), cancel=AsyncMock()
    )
    _consume(runtime, conn)
    lease = runtime.handle.active_lease
    lease.cleanup_thread.join(timeout=5)
    _wait(lambda: db.get_studio_chat_session(sid)["status"] == "error")

    assert lease.state == "FATAL" and process.kill_calls == 1
    assert conn.prompt.await_count == 0
    assert len(_dropped(db, sid, message["id"])) == 1
    assert _unknowns(db, sid, message["id"]) == []
    assert runtime.inbound_pending == 0
    runtime.handle.callbacks.on_exit(close_initiated=False)
    assert reconcile_zombie_session(service, db.get_studio_chat_session(sid))
    assert len(_dropped(db, sid, message["id"])) == 1

    successor = SessionRuntime(Mock(), "successor-token")
    successor_process = _FakeProcess()
    successor.handle._process = successor_process
    with service._runtimes_lock:
        service._runtimes[sid] = successor
    runtime.handle.callbacks.on_exit(close_initiated=False)
    assert service.runtime(sid) is successor
    assert successor_process.kill_calls == 0 and not successor.closed


def test_cancel_during_baseline_recovery_keeps_unstarted_drop_semantics(
    admission, monkeypatch
) -> None:
    service, db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime)
    assert wake_session(service, sid, runtime, ["finished-task"])
    message = service.send_message(sid, workspace, "cancel baseline recovery")
    runtime.handle._queue.get_nowait()
    envelope = runtime.handle._queue.get_nowait()
    service._events().on_turn_end(sid, "end_turn")
    if runtime.empty_turn_timer is not None:
        runtime.empty_turn_timer.cancel()
    runtime.handle._queue.put(envelope)
    runtime.handle._queue.put(_CLOSE)
    drop_started, release_drop = threading.Event(), threading.Event()
    real_list = db.list_studio_chat_messages
    failed = []

    def fail_once(*args, **kwargs):
        if kwargs.get("limit") == 1 and not failed:
            failed.append(1)
            raise RuntimeError("transient baseline read failure")
        return real_list(*args, **kwargs)

    real_append = service.store.append_message

    def pause_drop(session_id, kind, role, content):
        if content.get("event") == "queued_dropped":
            drop_started.set()
            assert release_drop.wait(timeout=5)
        return real_append(session_id, kind, role, content)

    monkeypatch.setattr(db, "list_studio_chat_messages", fail_once)
    monkeypatch.setattr(service.store, "append_message", pause_drop)
    conn = SimpleNamespace(
        prompt=AsyncMock(return_value=SimpleNamespace(stop_reason="end_turn")), cancel=AsyncMock()
    )
    loop_thread = threading.Thread(target=_consume, args=(runtime, conn), daemon=True)
    loop_thread.start()
    assert drop_started.wait(timeout=2)
    lease = runtime.handle.active_lease
    cancel_thread = threading.Thread(target=service.cancel, args=(sid, workspace), daemon=True)
    cancel_thread.start()
    _wait(lambda: lease.cancel_epoch == 1, timeout=2)
    release_drop.set()
    loop_thread.join(timeout=5)
    cancel_thread.join(timeout=5)

    assert not loop_thread.is_alive() and not cancel_thread.is_alive()
    assert lease.state == "ABORTED"
    assert len(_dropped(db, sid, message["id"])) == 1
    assert _unknowns(db, sid, message["id"]) == []
    assert runtime.inbound_pending == 0
    assert conn.prompt.await_count == 0
    assert process.kill_calls == 0


def test_already_delivered_turn_uses_unknown_not_baseline_drop(admission) -> None:
    service, db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime)
    assert wake_session(service, sid, runtime, ["finished-task"])
    message = service.send_message(sid, workspace, "delivered before fatal")
    runtime.handle._queue.get_nowait()
    envelope = runtime.handle._queue.get_nowait()
    service._events().on_turn_end(sid, "end_turn")
    if runtime.empty_turn_timer is not None:
        runtime.empty_turn_timer.cancel()
    runtime.handle._queue.put(envelope)
    runtime.handle._queue.put(_CLOSE)

    async def response_after_fatal():
        return SimpleNamespace(stop_reason="end_turn")

    def prompt(*_args):
        runtime.handle.active_lease.fail_claim_uncertain("delivered fatal")
        return response_after_fatal()

    conn = SimpleNamespace(prompt=Mock(side_effect=prompt), cancel=AsyncMock())
    _consume(runtime, conn)
    lease = runtime.handle.active_lease
    lease.cleanup_thread.join(timeout=5)
    _wait(lambda: db.get_studio_chat_session(sid)["status"] == "error")

    assert len(_unknowns(db, sid, message["id"])) == 1
    assert _dropped(db, sid, message["id"]) == []
    assert runtime.inbound_pending == 0
    assert process.kill_calls == 1
