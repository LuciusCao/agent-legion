"""Bounded user-cancel and off-loop turn watchdog coverage (#1153 A2)."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from server.app.studio_chat import token_keepalive
from server.app.studio_chat.acp_session import _CLOSE
from server.app.studio_chat.background_wakeup import wake_session
from server.app.studio_chat.callbacks import ServiceCallbacks
from server.app.studio_chat.compaction import note_turn_closed
from server.app.studio_chat.runtime import SessionRuntime
from server.app.studio_chat.timing import StudioTimingConfig
from server.app.studio_chat.token_keepalive import keepalive_run_token
from server.app.studio_chat.turn_state import open_turn
from server.app.studio_chat.turn_watchdog import note_prompt_response
from tests.helpers import pid_is_running, wait_for_predicate
from tests.helpers import studio_chat_fixtures as fixtures

chat = fixtures.chat
admission = fixtures.admission


def _wait(condition, timeout: float = 10.0) -> None:
    wait_for_predicate(condition, timeout=timeout, interval=0.02)


def _runtime_lock_held(runtime) -> bool:
    acquired = runtime.lock.acquire(blocking=False)
    if acquired:
        runtime.lock.release()
        return False
    return True


def _events(service, sid: str, workspace: str, name: str) -> list[dict]:
    return [
        message["content"]
        for message in service.list_messages(sid, workspace)
        if message["kind"] == "status" and message["content"].get("event") == name
    ]


def _prompt_texts(script_path) -> list[str]:
    path = Path(str(script_path) + ".sink.jsonl")
    if not path.exists():
        return []
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return [
        "".join(block.get("text", "") for block in entry["received"]["params"].get("prompt", []))
        for entry in entries
        if entry.get("received", {}).get("method") == "session/prompt"
    ]


class _FakeProcess:
    def __init__(self) -> None:
        self.returncode = None
        self.kill_calls = 0
        self.killed = threading.Event()

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9
        self.killed.set()


def _install_fake_process(service, sid: str, runtime, timing: StudioTimingConfig) -> _FakeProcess:
    process = _FakeProcess()
    runtime.handle.timing = timing
    runtime.handle._process = process
    callbacks = ServiceCallbacks(service, sid)
    callbacks.runtime = runtime
    runtime.handle.callbacks = callbacks
    return process


def test_user_cancel_timeout_settles_queue_and_kills_direct_child(chat) -> None:
    service, _bus, register, workspace, user = chat
    service._settings.studio_timing = StudioTimingConfig(
        prompt_timeout_seconds=30, cancel_grace_seconds=0.05, close_grace_seconds=0.05
    )
    script_path = register(
        {
            "prompt_plans": [
                {"on_prompt": [], "wait_for_cancel": True, "ignore_cancel": True},
                {"on_prompt": [], "wait_for_cancel": True},
            ]
        }
    )
    sid = service.create_session(workspace, user, "fake-agent")["id"]
    runtime = service.runtime(sid)
    process = runtime.handle._process
    assert wake_session(service, sid, runtime, ["finished-task"])
    queued = service.send_message(sid, workspace, "must not start after cancel")
    _wait(lambda: len(_prompt_texts(script_path)) == 1)

    service.cancel(sid, workspace)

    _wait(
        lambda: (
            service.get_session(sid)["status"] == "error"
            and any(
                event["message_id"] == queued["id"]
                for event in _events(service, sid, workspace, "queued_dropped")
            )
            and not pid_is_running(process.pid)
        )
    )
    assert "user session/cancel" in service.get_session(sid)["error_detail"]
    assert not _events(service, sid, workspace, "queued_delivered")
    assert not runtime.background_wakeup_enabled
    assert service.runtime(sid) is None
    _wait(lambda: not runtime.handle._thread.is_alive())
    assert len(_prompt_texts(script_path)) == 1


@pytest.mark.parametrize("trigger", ["cancel", "timeout"])
def test_wedged_event_loop_is_cleaned_off_loop(admission, monkeypatch, trigger) -> None:
    service, _db, sid, workspace, runtime = admission
    process = _install_fake_process(
        service,
        sid,
        runtime,
        StudioTimingConfig(
            prompt_timeout_seconds=30 if trigger == "cancel" else 0.05,
            cancel_grace_seconds=0.05,
            close_grace_seconds=0.05,
        ),
    )
    handle = runtime.handle
    loop_started, release_loop = threading.Event(), threading.Event()

    async def wedged_run() -> None:
        with handle._state_lock:
            handle._loop = asyncio.get_running_loop()
            handle._conn = SimpleNamespace(cancel=AsyncMock())
            handle._acp_session_id = "acp-wedged"
        loop_started.set()
        release_loop.wait()

    monkeypatch.setattr(handle, "_run", wedged_run)
    message = service.send_message(sid, workspace, "loop will wedge")
    handle.start()
    assert loop_started.wait(timeout=2)
    try:
        if trigger == "cancel":
            service.cancel(sid, workspace)
        _wait(
            lambda: (
                process.killed.is_set()
                and service.runtime(sid) is None
                and service.get_session(sid)["status"] == "error"
            )
        )
        assert handle._thread.is_alive()
        expected = "user session/cancel" if trigger == "cancel" else "after session/cancel"
        assert expected in service.get_session(sid)["error_detail"]
        assert any(
            event.get("message_id") == message["id"] and event.get("outcome") == "unknown"
            for event in _events(service, sid, workspace, "error")
        )
    finally:
        release_loop.set()
        handle._thread.join(timeout=2)
    assert not handle._thread.is_alive()


@pytest.mark.parametrize("trigger", ["cancel", "timeout"])
def test_owned_callback_lock_does_not_block_off_loop_kill_or_deferred_settlement(
    admission, monkeypatch, trigger
) -> None:
    service, _db, sid, workspace, runtime = admission
    process = _install_fake_process(
        service,
        sid,
        runtime,
        StudioTimingConfig(
            prompt_timeout_seconds=30 if trigger == "cancel" else 0.05,
            cancel_grace_seconds=0.05,
            close_grace_seconds=0.05,
        ),
    )
    message = service.send_message(sid, workspace, f"owned callback {trigger}")
    watchdog = runtime.turn_watchdog
    if trigger == "cancel":
        note_prompt_response(runtime.handle)
    callback_started, release_callback = threading.Event(), threading.Event()
    real_on_turn_end = service._on_turn_end

    def blocking_on_turn_end(*args, **kwargs):
        callback_started.set()
        assert release_callback.wait(timeout=10)
        real_on_turn_end(*args, **kwargs)

    monkeypatch.setattr(service, "_on_turn_end", blocking_on_turn_end)
    callback_thread = threading.Thread(
        target=runtime.handle.callbacks.on_turn_end,
        args=("end_turn",),
        name="test-owned-callback",
        daemon=True,
    )
    callback_thread.start()
    assert callback_started.wait(timeout=2)
    assert _runtime_lock_held(runtime)
    handle_state = runtime.handle._state_lock
    handle_state.acquire()
    state_held = True
    cancel_thread = None
    try:
        if trigger == "cancel":
            cancel_thread = threading.Thread(
                target=service.cancel, args=(sid, workspace), name="test-user-cancel", daemon=True
            )
            cancel_thread.start()
            _wait(lambda: watchdog._cancel_epoch == 1, timeout=2)
            assert cancel_thread.is_alive()

        _wait(lambda: process.killed.is_set() and watchdog._claimed, timeout=2)
        assert _runtime_lock_held(runtime) and callback_thread.is_alive()
        assert handle_state.locked()
        assert watchdog.cleanup_thread.is_alive()
        assert service.runtime(sid) is runtime
        assert service.get_session(sid)["status"] == "running"
        handle_state.release()
        state_held = False
        assert not runtime.handle.send_prompt("must stay fenced while DB settlement waits")
    finally:
        if state_held:
            handle_state.release()
        release_callback.set()
        callback_thread.join(timeout=2)
        if cancel_thread is not None:
            cancel_thread.join(timeout=2)

    assert not callback_thread.is_alive()
    watchdog.cleanup_thread.join(timeout=2)
    _wait(lambda: service.runtime(sid) is None and service.get_session(sid)["status"] == "error")
    expected = "user session/cancel" if trigger == "cancel" else "after session/cancel"
    assert expected in service.get_session(sid)["error_detail"]
    assert any(
        event.get("message_id") == message["id"]
        for event in _events(service, sid, workspace, "turn_end")
    )

    successor = SessionRuntime(Mock(), "successor-token")
    successor_process = _FakeProcess()
    successor.handle._process = successor_process
    with service._runtimes_lock:
        service._runtimes[sid] = successor
    runtime.handle.callbacks.on_exit(close_initiated=False)
    assert service.runtime(sid) is successor
    assert not successor.closed and successor_process.kill_calls == 0


def test_quiet_turn_before_total_deadline_is_not_killed(admission) -> None:
    service, _db, sid, workspace, runtime = admission
    process = _install_fake_process(
        service, sid, runtime, StudioTimingConfig(prompt_timeout_seconds=30)
    )
    service.send_message(sid, workspace, "quiet but below the explicit bound")
    watchdog = runtime.turn_watchdog

    watchdog._fire(watchdog._generation, "timeout", 0)

    assert process.kill_calls == 0
    assert service.runtime(sid) is runtime
    assert runtime.turn_open
    note_turn_closed(runtime)


def test_prompt_response_disarms_total_timeout_but_not_user_cancel(admission, monkeypatch) -> None:
    service, _db, sid, workspace, runtime = admission
    _process = _install_fake_process(
        service, sid, runtime, StudioTimingConfig(prompt_timeout_seconds=30)
    )
    service.send_message(sid, workspace, "response before slow terminal persistence")
    first = runtime.turn_watchdog
    first_escalate = Mock()
    monkeypatch.setattr(first, "_escalate", first_escalate)
    note_prompt_response(runtime.handle)

    first._fire(first._generation, first._reason, first._cancel_epoch)

    first_escalate.assert_not_called()
    note_turn_closed(runtime)
    open_turn(runtime, "cancel remains bounded after response")
    second = runtime.turn_watchdog
    service.cancel(sid, workspace)
    note_prompt_response(runtime.handle)
    assert not second._disarmed
    note_turn_closed(runtime)


def test_response_then_new_cancel_arms_independent_grace(admission) -> None:
    service, _db, sid, workspace, runtime = admission
    _process = _install_fake_process(
        service, sid, runtime, StudioTimingConfig(prompt_timeout_seconds=30)
    )
    service.send_message(sid, workspace, "response precedes user cancel")
    watchdog = runtime.turn_watchdog
    note_prompt_response(runtime.handle)
    assert watchdog._total_deadline is None and runtime.turn_open

    service.cancel(sid, workspace)

    assert watchdog._cancel_epoch == 1
    assert watchdog._reason == "cancel"
    assert watchdog._deadline is not None
    assert not watchdog._disarmed
    note_turn_closed(runtime)


def test_response_then_close_does_not_arm_late_cancel(admission) -> None:
    service, _db, sid, workspace, runtime = admission
    process = _install_fake_process(
        service, sid, runtime, StudioTimingConfig(prompt_timeout_seconds=30)
    )
    service.send_message(sid, workspace, "close before late cancel")
    watchdog = runtime.turn_watchdog
    note_prompt_response(runtime.handle)
    service._events().on_turn_end(sid, "end_turn")

    result = service.cancel(sid, workspace)

    assert result["status"] == "idle"
    assert watchdog._cancel_epoch == 0
    assert process.kill_calls == 0
    assert watchdog.cleanup_thread is None


def test_prompt_loop_disarms_total_timeout_before_turn_end(admission, monkeypatch) -> None:
    service, _db, sid, workspace, runtime = admission
    _process = _install_fake_process(
        service, sid, runtime, StudioTimingConfig(prompt_timeout_seconds=30)
    )
    service.send_message(sid, workspace, "quick response")
    watchdog = runtime.turn_watchdog
    observed = []
    real_on_turn_end = service._on_turn_end

    def recording_on_turn_end(session_id, stop_reason, *, timed_out=False):
        observed.append(watchdog._total_deadline is None)
        real_on_turn_end(session_id, stop_reason, timed_out=timed_out)

    class QuickConn:
        async def prompt(self, *args):
            return SimpleNamespace(stop_reason="end_turn")

    monkeypatch.setattr(service, "_on_turn_end", recording_on_turn_end)
    runtime.handle._queue.put(_CLOSE)

    asyncio.run(runtime.handle._prompt_loop(QuickConn(), "acp-session"))

    assert observed == [True]


def test_watchdog_fences_next_admission_before_cleanup(admission, monkeypatch) -> None:
    service, _db, sid, workspace, runtime = admission
    _process = _install_fake_process(
        service, sid, runtime, StudioTimingConfig(prompt_timeout_seconds=30)
    )
    service.send_message(sid, workspace, "fence before cleanup")
    watchdog = runtime.turn_watchdog
    watchdog._deadline = 0
    escalate = Mock()
    monkeypatch.setattr(watchdog, "_escalate", escalate)

    watchdog._fire(watchdog._generation, watchdog._reason, watchdog._cancel_epoch)

    assert runtime.handle._stop_requested
    assert runtime.handle._watchdog_candidate is watchdog
    assert "prompt turn wedged" in runtime.handle.transport_failure()
    assert not runtime.handle.send_prompt("must not overtake fatal cleanup")
    watchdog.cleanup_thread.join(timeout=2)
    escalate.assert_called_once()


def test_candidate_during_accept_cannot_enter_prompt_queue(admission) -> None:
    _service, _db, _sid, _workspace, runtime = admission
    accepted = []

    def accept() -> None:
        accepted.append(True)
        runtime.handle._watchdog_candidate = object()

    assert not runtime.handle.send_prompt("candidate lands during accept", accept=accept)
    assert accepted == [True]
    assert runtime.handle._queue.empty()
    runtime.handle._watchdog_candidate = None


def test_stale_cancel_and_process_identity_cannot_kill_next_turn(admission, monkeypatch) -> None:
    service, _db, sid, workspace, runtime = admission
    first = _install_fake_process(
        service, sid, runtime, StudioTimingConfig(prompt_timeout_seconds=30)
    )
    service.send_message(sid, workspace, "first turn")
    service.cancel(sid, workspace)
    old = runtime.turn_watchdog
    old_generation = old._generation
    old_escalate = Mock()
    monkeypatch.setattr(old, "_escalate", old_escalate)
    note_turn_closed(runtime)

    second = _FakeProcess()
    runtime.handle._process = second
    open_turn(runtime, "next turn")
    old._fire(old_generation, "cancel", 1)

    assert first.kill_calls == second.kill_calls == 0
    assert runtime.turn_open and runtime.turn_watchdog is not old
    old_escalate.assert_not_called()

    current = runtime.turn_watchdog
    replacement = _FakeProcess()
    runtime.handle._process = replacement
    current._deadline = 0
    current_escalate = Mock()
    monkeypatch.setattr(current, "_escalate", current_escalate)
    current._fire(current._generation, current._reason, current._cancel_epoch)
    assert replacement.kill_calls == 0
    current_escalate.assert_not_called()
    note_turn_closed(runtime)


@pytest.mark.no_db
def test_keepalive_threshold_uses_handle_prompt_timeout(monkeypatch) -> None:
    runtime = SessionRuntime(SimpleNamespace(prompt_timeout_seconds=7200), "raw-token")
    backend = Mock()
    backend.runtime.return_value = runtime
    backend.db.get_scoped_token_user.return_value = {"id": "user"}
    renew = Mock(return_value=True)
    monkeypatch.setattr(token_keepalive, "renew_scoped_token", renew)

    keepalive_run_token(backend, "session")

    assert renew.call_args.kwargs["threshold"] == timedelta(seconds=7500)


def test_turn_timeout_text_uses_handle_duration(admission) -> None:
    service, db, sid, workspace, runtime = admission
    runtime.handle.timing = StudioTimingConfig(prompt_timeout_seconds=45)
    db.update_studio_chat_session(sid, status="running")

    service._events().on_turn_end(sid, "cancelled", timed_out=True)

    [event] = _events(service, sid, workspace, "turn_timeout")
    assert event["detail"] == "运行超过 45 秒已被终止"


def test_existing_handle_keeps_timing_snapshot(admission) -> None:
    service, _db, _sid, _workspace, runtime = admission
    original = StudioTimingConfig(prompt_timeout_seconds=12)
    runtime.handle.timing = original

    service._settings.studio_timing = StudioTimingConfig(prompt_timeout_seconds=99)

    assert runtime.handle.timing is original
    assert runtime.handle.prompt_timeout_seconds == 12


def test_close_uses_configured_grace_and_revalidates_process(admission) -> None:
    _service, _db, _sid, _workspace, runtime = admission
    handle = runtime.handle
    handle.timing = StudioTimingConfig(close_grace_seconds=0.25)
    process, replacement = _FakeProcess(), _FakeProcess()

    class FakeThread:
        def __init__(self) -> None:
            self.joins = []

        def join(self, timeout) -> None:
            self.joins.append(timeout)

        def is_alive(self) -> bool:
            return True

    thread = FakeThread()
    handle._thread, handle._process = thread, process
    handle.close()

    assert thread.joins == [0.25, 0.5]
    assert process.kill_calls == 1

    handle._closed = False
    handle._process = replacement
    handle._kill_process(expected=(thread, process))
    assert replacement.kill_calls == 0
    handle._closed = True
