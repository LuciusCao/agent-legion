"""Deterministic response/cancel timer generation and deadline coverage."""

from __future__ import annotations

import threading

from server.app.studio_chat import turn_lease
from server.app.studio_chat.callbacks import ServiceCallbacks
from server.app.studio_chat.compaction import note_turn_closed
from server.app.studio_chat.timing import StudioTimingConfig
from server.app.studio_chat.turn_state import open_turn
from server.app.studio_chat.turn_watchdog import begin_turn_admission, note_prompt_response
from tests.helpers import studio_chat_fixtures as fixtures
from tests.helpers import wait_for_predicate

admission = fixtures.admission


class _FakeProcess:
    def __init__(self) -> None:
        self.returncode = None
        self.kill_calls = 0
        self.killed = threading.Event()

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9
        self.killed.set()


class _FakeTimer:
    created = []
    clock = [0.0]

    def __init__(self, interval, function, args):
        self.interval = interval
        self.function = function
        self.args = args
        self.created_at = self.clock[0]
        self.cancelled = False
        self.fired = False
        self.name = None
        self.daemon = None
        type(self).created.append(self)

    def cancel(self) -> None:
        self.cancelled = True

    def start(self) -> None:
        pass

    def fire_due(self) -> None:
        if (
            not self.cancelled
            and not self.fired
            and self.clock[0] >= self.created_at + self.interval
        ):
            self.fired = True
            self.function(*self.args)


def _install(service, sid: str, runtime, monkeypatch) -> _FakeProcess:
    process = _FakeProcess()
    runtime.handle.timing = StudioTimingConfig(
        prompt_timeout_seconds=10, cancel_grace_seconds=5, close_grace_seconds=0.05
    )
    runtime.handle._process = process
    callbacks = ServiceCallbacks(service, sid)
    callbacks.runtime = runtime
    runtime.handle.callbacks = callbacks
    _FakeTimer.created = []
    _FakeTimer.clock = [100.0]
    monkeypatch.setattr(turn_lease.threading, "Timer", _FakeTimer)
    monkeypatch.setattr(turn_lease.time, "monotonic", lambda: _FakeTimer.clock[0])
    return process


def _fire_due() -> None:
    for timer in list(_FakeTimer.created):
        timer.fire_due()


def _unknowns(db, sid: str, message_id: str) -> list[dict]:
    return [
        message["content"]
        for message in db.list_studio_chat_messages(sid)
        if message["kind"] == "status"
        and message["content"].get("event") == "error"
        and message["content"].get("message_id") == message_id
        and message["content"].get("outcome") == "unknown"
    ]


def test_response_rebuilds_cancel_timer_after_total_deadline(admission, monkeypatch) -> None:
    service, db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime, monkeypatch)
    message = service.send_message(sid, workspace, "cancel after total deadline")
    lease = runtime.handle.active_lease
    lease.request_cancel(112.0)
    original_deadline = lease.cancel_deadline
    original_epoch = lease.cancel_epoch
    note_prompt_response(runtime.handle, lease)

    assert original_deadline == 117.0
    assert lease.cancel_deadline == original_deadline
    assert lease.cancel_epoch == original_epoch
    assert lease.deadline == original_deadline and lease.reason == "cancel"
    timer_count = len(_FakeTimer.created)
    note_prompt_response(runtime.handle, lease)
    assert len(_FakeTimer.created) == timer_count

    _FakeTimer.clock[0] = 116.0
    _fire_due()
    assert process.kill_calls == 0
    assert lease.state == "RESPONDED" and lease.cleanup_thread is None
    assert db.get_studio_chat_session(sid)["status"] == "running"

    _FakeTimer.clock[0] = 117.0
    _fire_due()
    lease.cleanup_thread.join(timeout=5)
    wait_for_predicate(
        lambda: (
            service.runtime(sid) is None and db.get_studio_chat_session(sid)["status"] == "error"
        ),
        timeout=3,
    )
    assert process.kill_calls == 1
    assert lease.state == "FATAL"
    assert len(_unknowns(db, sid, message["id"])) == 1


def test_response_close_and_old_timer_cannot_harm_successor(admission, monkeypatch) -> None:
    service, db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime, monkeypatch)
    service.send_message(sid, workspace, "close before old cancel timer")
    first = runtime.handle.active_lease
    first.request_cancel(112.0)
    note_prompt_response(runtime.handle, first)
    _FakeTimer.clock[0] = 116.0
    service._events().on_turn_end(sid, "end_turn")

    assert first.state == "CLOSED" and process.kill_calls == 0
    assert db.get_studio_chat_session(sid)["status"] == "idle"
    successor = begin_turn_admission(runtime)
    assert successor is not None and successor.cancel_epoch == 0
    open_turn(runtime, "successor after old timer", owner=successor)
    _FakeTimer.clock[0] = 117.0
    _fire_due()

    assert process.kill_calls == 0
    assert successor.state == "OPEN" and successor.cancel_epoch == 0
    assert runtime.turn_open
    note_turn_closed(runtime)
