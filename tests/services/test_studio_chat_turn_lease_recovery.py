"""r5 adversarial regressions for lease terminals, activation and claim recovery."""

from __future__ import annotations

import asyncio
import contextlib
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from acp import RequestError

from server.app.studio_chat import admission_turn as direct_module
from server.app.studio_chat import empty_turn_retry as retry_module
from server.app.studio_chat import inbound_delivery as queued_module
from server.app.studio_chat import turn_state
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


def _message_events(db, sid: str, name: str, message_id: str) -> list[dict]:
    return [event for event in _events(db, sid, name) if event.get("message_id") == message_id]


def _prepare_queued(service, sid: str, workspace: str, runtime):
    assert wake_session(service, sid, runtime, ["finished-task"])
    message = service.send_message(sid, workspace, "queued turn")
    runtime.handle._queue.get_nowait()
    envelope = runtime.handle._queue.get_nowait()
    service._events().on_turn_end(sid, "end_turn")
    if runtime.empty_turn_timer is not None:
        runtime.empty_turn_timer.cancel()
    return message, envelope


def _prepare_empty(service, sid: str, workspace: str, runtime):
    message = service.send_message(sid, workspace, "empty retry turn")
    original = runtime.handle._queue.get_nowait()
    service._events().on_turn_end(sid, "end_turn")
    if runtime.empty_turn_timer is not None:
        runtime.empty_turn_timer.cancel()
    runtime.empty_turn_retry = (message["id"], "empty retry turn", original.text)
    return message


@pytest.mark.parametrize("queued", [False, True], ids=["direct", "queued"])
@pytest.mark.parametrize("outcome", ["response", "fatal-then-error", "error-then-fatal"])
def test_pre_fatal_rpc_never_writes_a_normal_terminal(
    admission, monkeypatch, queued: bool, outcome: str
) -> None:
    service, db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime)
    if queued:
        message, envelope = _prepare_queued(service, sid, workspace, runtime)
        runtime.handle._queue.put(envelope)
    else:
        message = service.send_message(sid, workspace, "direct pre-fatal turn")
    runtime.handle._queue.put(_CLOSE)
    cleanup_attempted, release_cleanup = threading.Event(), threading.Event()
    real_rpc_mutex = runtime.handle.rpc_mutex

    class ProbeMutex:
        def __enter__(self):
            if threading.current_thread().name == "studio-turn-lease-cleanup":
                cleanup_attempted.set()
                assert release_cleanup.wait(timeout=5)
            return real_rpc_mutex.__enter__()

        def __exit__(self, *args):
            return real_rpc_mutex.__exit__(*args)

        def locked(self):
            return real_rpc_mutex.locked()

    runtime.handle.rpc_mutex = runtime.handle._prompt_start_gate.rpc_mutex = ProbeMutex()
    leases = []

    def fire(lease) -> None:
        lease.deadline = 0
        lease._fire(lease.generation, lease.reason, lease.cancel_epoch)

    async def immediate_response():
        return SimpleNamespace(stop_reason="end_turn")

    async def immediate_error(lease, error_first: bool):
        try:
            raise RequestError(-32000, "ordinary refusal")
        finally:
            if error_first:
                fire(lease)

    def prompt(*_args):
        lease = runtime.handle.active_lease
        leases.append(lease)
        if outcome != "error-then-fatal":
            fire(lease)
        if outcome == "response":
            return immediate_response()
        return immediate_error(lease, outcome == "error-then-fatal")

    conn = SimpleNamespace(prompt=Mock(side_effect=prompt), cancel=AsyncMock())
    lease = None
    try:
        with contextlib.suppress(AcpConnectionLost):
            asyncio.run(runtime.handle._prompt_loop(conn, "acp-session"))
        assert cleanup_attempted.wait(timeout=2)
        [lease] = leases
        assert lease.state == "KILLING"
        assert process.kill_calls == 1
        assert runtime.inbound_messages.get(message["id"]) == "delivered"
        assert db.get_studio_chat_session(sid)["status"] == "running"
        assert _message_events(db, sid, "turn_end", message["id"]) == []
        assert not [
            event for event in _events(db, sid, "error") if event.get("outcome") == "failed"
        ]
        assert _unknowns(db, sid, message["id"]) == []
    finally:
        release_cleanup.set()
        if lease is not None and lease.cleanup_thread is not None:
            lease.cleanup_thread.join(timeout=5)

    assert lease is not None and not lease.cleanup_thread.is_alive()
    _wait(
        lambda: (
            service.runtime(sid) is None and db.get_studio_chat_session(sid)["status"] == "error"
        )
    )
    assert len(_unknowns(db, sid, message["id"])) == 1
    assert _message_events(db, sid, "turn_end", message["id"]) == []
    assert not [event for event in _events(db, sid, "error") if event.get("outcome") == "failed"]
    assert conn.prompt.call_count == 1


@pytest.mark.parametrize("path", ["direct", "queued", "empty", "background"])
def test_real_cancel_timer_between_activate_and_open_mirror_kills_current_lease(
    admission, monkeypatch, path: str
) -> None:
    service, db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime, cancel_grace=0.05)
    message = None
    if path == "queued":
        message, envelope = _prepare_queued(service, sid, workspace, runtime)
    elif path == "empty":
        message = _prepare_empty(service, sid, workspace, runtime)

    activated, release_open = threading.Event(), threading.Event()
    real_activate = turn_state.activate_turn_lease

    def paused_activate(owner):
        activated_owner = real_activate(owner)
        activated.set()
        assert release_open.wait(timeout=5)
        return activated_owner

    monkeypatch.setattr(turn_state, "activate_turn_lease", paused_activate)
    results, errors = [], []

    def run() -> None:
        try:
            if path == "direct":
                results.append(service.send_message(sid, workspace, "activation race"))
            elif path == "queued":
                results.append(envelope.before_start())
            elif path == "empty":
                results.append(retry_module.retry_empty_turn(service, sid, runtime))
            else:
                results.append(wake_session(service, sid, runtime, ["finished-task"]))
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    assert activated.wait(timeout=2)
    lease = runtime.handle.active_lease
    assert lease.state == "OPEN" and not runtime.turn_open
    cancel_thread = threading.Thread(target=service.cancel, args=(sid, workspace), daemon=True)
    cancel_thread.start()
    try:
        _wait(lambda: lease.cancel_epoch == 1, timeout=2)
        _wait(lambda: process.killed.is_set() and lease.state in ("KILLING", "FATAL"), timeout=2)
        assert worker.is_alive() and not runtime.turn_open
        assert runtime.handle._queue.empty()
        assert lease.cleanup_thread is not None
    finally:
        release_open.set()
        worker.join(timeout=5)
        cancel_thread.join(timeout=5)
        if lease.cleanup_thread is not None:
            lease.cleanup_thread.join(timeout=5)

    assert not worker.is_alive() and not cancel_thread.is_alive()
    assert process.kill_calls == 1 and lease.state == "FATAL"
    _wait(
        lambda: (
            service.runtime(sid) is None and db.get_studio_chat_session(sid)["status"] == "error"
        )
    )
    if path != "background":
        if message is None:
            [message] = [row for row in db.list_studio_chat_messages(sid) if row["role"] == "user"]
        assert len(_unknowns(db, sid, message["id"])) == 1

    successor = SessionRuntime(Mock(), "successor-token")
    successor_process = _FakeProcess()
    successor.handle._process = successor_process
    with service._runtimes_lock:
        service._runtimes[sid] = successor
    lease._fire(lease.generation, lease.reason, lease.cancel_epoch)
    assert successor_process.kill_calls == 0 and not successor.closed


@pytest.mark.parametrize("path", ["direct", "empty"])
def test_post_commit_claim_exception_recovers_same_lease(admission, monkeypatch, path: str) -> None:
    service, db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime)
    if path == "empty":
        message = _prepare_empty(service, sid, workspace, runtime)
        real_claim = db.claim_studio_chat_turn_with_token
    else:
        real_claim = db.accept_studio_chat_message

    def committed_then_error(*args, **kwargs):
        real_claim(*args, **kwargs)
        raise RuntimeError("injected post-commit uncertainty")

    if path == "empty":
        monkeypatch.setattr(db, "claim_studio_chat_turn_with_token", committed_then_error)
    else:
        monkeypatch.setattr(db, "accept_studio_chat_message", committed_then_error)

    if path == "direct":
        message = service.send_message(sid, workspace, "committed direct input")
    else:
        assert retry_module.retry_empty_turn(service, sid, runtime)

    lease = runtime.handle.active_lease
    assert lease.state == "OPEN" and runtime.turn_open
    assert runtime.handle._queue.qsize() == 1
    assert db.get_studio_chat_session(sid)["status"] == "running"
    assert db.count_studio_chat_user_messages(sid) == 1
    lease.deadline = 0
    lease._fire(lease.generation, lease.reason, lease.cancel_epoch)
    lease.cleanup_thread.join(timeout=5)
    _wait(
        lambda: (
            service.runtime(sid) is None and db.get_studio_chat_session(sid)["status"] == "error"
        )
    )
    assert process.kill_calls == 1
    assert len(_unknowns(db, sid, message["id"])) == 1
    runtime.handle.callbacks.on_exit(close_initiated=False)
    assert reconcile_zombie_session(service, db.get_studio_chat_session(sid))
    assert len(_unknowns(db, sid, message["id"])) == 1
    assert db.count_studio_chat_user_messages(sid) == 1


@pytest.mark.parametrize("path", ["direct", "queued", "empty", "background"])
def test_open_mirror_failure_preserves_accepted_identity_for_fatal_settlement(
    admission, monkeypatch, path: str
) -> None:
    service, db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime)
    message = None
    if path == "queued":
        message, envelope = _prepare_queued(service, sid, workspace, runtime)
        target_module = queued_module
    elif path == "empty":
        message = _prepare_empty(service, sid, workspace, runtime)
        target_module = retry_module
    elif path == "direct":
        target_module = direct_module
    else:
        from server.app.studio_chat import background_delivery

        target_module = background_delivery
    real_open = target_module.open_turn

    def open_then_error(*args, **kwargs):
        real_open(*args, **kwargs)
        raise RuntimeError("injected open mirror failure")

    monkeypatch.setattr(target_module, "open_turn", open_then_error)
    results, errors = [], []
    try:
        if path == "direct":
            service.send_message(sid, workspace, "open failure direct")
        elif path == "queued":
            results.append(envelope.before_start())
        elif path == "empty":
            retry_module.retry_empty_turn(service, sid, runtime)
        else:
            wake_session(service, sid, runtime, ["finished-task"])
    except RuntimeError as exc:
        errors.append(exc)

    lease = runtime.handle.active_lease
    assert lease.state != "ABORTED"
    if lease.cleanup_thread is not None:
        lease.cleanup_thread.join(timeout=5)
    _wait(lambda: process.killed.is_set() and lease.state == "FATAL", timeout=2)
    _wait(
        lambda: (
            service.runtime(sid) is None and db.get_studio_chat_session(sid)["status"] == "error"
        )
    )
    assert process.kill_calls == 1
    assert results == ([False] if path == "queued" else [])
    assert len(errors) == (0 if path == "queued" else 1)
    remaining = []
    while not runtime.handle._queue.empty():
        remaining.append(runtime.handle._queue.get_nowait())
    assert all(item is _CLOSE for item in remaining)
    if path != "background":
        if message is None:
            [message] = [row for row in db.list_studio_chat_messages(sid) if row["role"] == "user"]
        assert len(_unknowns(db, sid, message["id"])) == 1


@pytest.mark.parametrize("path", ["direct", "empty", "background"])
def test_proven_unclaimed_exception_aborts_without_kill(admission, monkeypatch, path: str) -> None:
    service, db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime)
    if path == "empty":
        _prepare_empty(service, sid, workspace, runtime)

    def fail_before_claim(*_args, **_kwargs):
        raise RuntimeError("definitely no commit")

    def operation():
        if path == "direct":
            return service.send_message(sid, workspace, "unclaimed")
        if path == "empty":
            return retry_module.retry_empty_turn(service, sid, runtime)
        return wake_session(service, sid, runtime, ["finished-task"])

    if path == "direct":
        monkeypatch.setattr(db, "accept_studio_chat_message", fail_before_claim)
    elif path == "empty":
        monkeypatch.setattr(db, "claim_studio_chat_turn_with_token", fail_before_claim)
    else:
        monkeypatch.setattr(db, "claim_studio_chat_turn", fail_before_claim)

    with pytest.raises(RuntimeError, match="definitely no commit"):
        operation()
    lease = runtime.handle.active_lease
    assert lease.state == "ABORTED"
    assert process.kill_calls == 0 and runtime.handle.fatal_lease is None
    assert runtime.handle._queue.empty()
    assert db.get_studio_chat_session(sid)["status"] == "idle"
