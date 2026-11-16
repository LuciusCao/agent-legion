"""Prompt-start and accept-time cancellation interleavings (#1153 A2 r3)."""

from __future__ import annotations

import asyncio
import contextlib
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from server.app.services.job_errors import ConflictError
from server.app.studio_chat import admission_turn as direct_module
from server.app.studio_chat import empty_turn_retry as retry_module
from server.app.studio_chat import inbound_delivery as queued_module
from server.app.studio_chat.acp_session import _CLOSE
from server.app.studio_chat.background_wakeup import wake_session
from server.app.studio_chat.callbacks import ServiceCallbacks
from server.app.studio_chat.compaction import note_turn_closed
from server.app.studio_chat.process_exit import AcpConnectionLost
from server.app.studio_chat.prompt_admission import QueuedPrompt
from server.app.studio_chat.timing import StudioTimingConfig
from server.app.studio_chat.turn_lease import TurnLease
from server.app.studio_chat.turn_watchdog import begin_turn_admission, claim_turn_lease
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
    watchdog = runtime.handle.active_lease
    fatal_settled = threading.Event()
    monkeypatch.setattr(watchdog, "_escalate", Mock(side_effect=lambda *_args: fatal_settled.set()))
    before_start = Mock(return_value=True)
    if queued:
        runtime.handle._queue.get_nowait()
        runtime.handle._queue.put(QueuedPrompt("queued text", watchdog, before_start=before_start))
    runtime.handle._queue.put(QueuedPrompt("must not start after FATAL", watchdog))
    runtime.handle._queue.put(_CLOSE)

    pre_fatal_completed = []
    gate_observed = []

    async def complete_pre_fatal_rpc():
        settled = await asyncio.to_thread(fatal_settled.wait, 2)
        pre_fatal_completed.append((settled, watchdog.state))
        return SimpleNamespace(stop_reason="end_turn")

    def prompt(*_args):
        if conn.prompt.call_count == 1:
            watchdog.deadline = 0
            watchdog._fire(watchdog.generation, watchdog.reason, watchdog.cancel_epoch)
            gate_observed.append(
                (
                    cleanup_rpc_attempted.wait(timeout=2),
                    process.kill_calls,
                    watchdog.cleanup_thread.is_alive(),
                    watchdog.state,
                    runtime.handle.rpc_mutex.locked(),
                )
            )
            return complete_pre_fatal_rpc()
        return asyncio.sleep(0, result=SimpleNamespace(stop_reason="end_turn"))

    conn = SimpleNamespace(prompt=Mock(side_effect=prompt), cancel=AsyncMock())
    cleanup_rpc_attempted = threading.Event()
    real_rpc_mutex = runtime.handle.rpc_mutex

    class ProbeMutex:
        def __enter__(self):
            if threading.current_thread().name == "studio-turn-lease-cleanup":
                cleanup_rpc_attempted.set()
            return real_rpc_mutex.__enter__()

        def __exit__(self, *args):
            return real_rpc_mutex.__exit__(*args)

        def locked(self):
            return real_rpc_mutex.locked()

    runtime.handle.rpc_mutex = runtime.handle._prompt_start_gate.rpc_mutex = ProbeMutex()

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

    assert gate_observed == [(True, 1, True, "KILLING", True)]
    assert pre_fatal_completed == [(True, "FATAL")]
    assert process.kill_calls == 1
    assert conn.prompt.call_count == 1
    assert before_start.call_count == (1 if queued else 0)
    watchdog.cleanup_thread.join(timeout=2)
    assert watchdog.state == "FATAL"


def test_prompt_creation_wedged_does_not_delay_kill(admission, monkeypatch) -> None:
    service, _db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime)
    service.send_message(sid, workspace, "wedge inside prompt creation")
    watchdog = runtime.handle.active_lease
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
    watchdog.deadline = 0
    fire_thread = threading.Thread(
        target=watchdog._fire,
        args=(watchdog.generation, watchdog.reason, watchdog.cancel_epoch),
        daemon=True,
    )
    fire_thread.start()
    try:
        assert process.killed.wait(timeout=2)
        assert process.kill_calls == 1
        assert runtime.handle.rpc_mutex.locked()
        assert loop_thread.is_alive()
    finally:
        release.set()
        fire_thread.join(timeout=2)
        loop_thread.join(timeout=2)
    watchdog.cleanup_thread.join(timeout=2)
    assert not fire_thread.is_alive() and not loop_thread.is_alive()


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
    if not queued:
        real_db_accept = db.accept_studio_chat_message

        def accept_with_lease(*args):
            lease = runtime.handle.active_lease
            assert lease is not None and lease.state == "CLAIMING"
            return real_db_accept(*args)

        monkeypatch.setattr(db, "accept_studio_chat_message", accept_with_lease)
    message = None
    started = []
    if queued:
        assert wake_session(service, sid, runtime, ["finished-task"])
        message = service.send_message(sid, workspace, "queued cancel binding")
        _background_prompt, queued_prompt = (
            runtime.handle._queue.get_nowait(),
            runtime.handle._queue.get_nowait(),
        )
        assert queued_prompt.lease is None
        service._events().on_turn_end(sid, "end_turn")
        before_start = queued_prompt.before_start
        worker = threading.Thread(target=lambda: started.append(before_start()), daemon=True)
    else:
        worker = threading.Thread(
            target=lambda: service.send_message(sid, workspace, "direct cancel binding"),
            daemon=True,
        )
    worker.start()
    assert accepted.wait(timeout=2)
    assert db.get_studio_chat_session(sid)["status"] == "running"
    claiming = runtime.handle.active_lease
    assert claiming.state == "CLAIMING"
    assert runtime.turn_owner is claiming

    cancel_thread = threading.Thread(target=service.cancel, args=(sid, workspace), daemon=True)
    cancel_thread.start()
    _wait(lambda: getattr(runtime.handle.active_lease, "requested_at", None) is not None, timeout=2)
    release_accept.set()
    worker.join(timeout=2)
    cancel_thread.join(timeout=2)
    watchdog = runtime.handle.active_lease
    if queued:
        assert started == [watchdog]
    first_deadline = watchdog.deadline
    assert watchdog.cancel_epoch == 1
    assert first_deadline is not None

    service.cancel(sid, workspace)
    assert watchdog.cancel_epoch == 2
    assert watchdog.deadline == first_deadline
    watchdog.deadline = 0
    watchdog._fire(watchdog.generation, watchdog.reason, watchdog.cancel_epoch)
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
    assert getattr(runtime.handle.active_lease, "requested_at", None) is None
    assert not hasattr(runtime.handle, "pending_cancel")
    assert not hasattr(runtime, "pending_cancel")

    service.send_message(sid, workspace, "ordinary next turn")
    assert runtime.handle.active_lease.cancel_epoch == 0
    assert getattr(runtime.handle.active_lease, "requested_at", None) is None
    assert not hasattr(runtime.handle, "pending_cancel")


@pytest.mark.parametrize("open_first", [False, True], ids=["arm-CLAIMING", "arm-OPEN"])
def test_cancel_read_and_open_interleavings_keep_the_same_lease(
    admission, monkeypatch, open_first
) -> None:
    service, _db, sid, workspace, runtime = admission
    _process = _install(service, sid, runtime, cancel_grace=5)
    accepted, release_accept = threading.Event(), threading.Event()
    cancel_read, release_cancel = threading.Event(), threading.Event()
    real_open_turn = direct_module.open_turn
    real_request_cancel = TurnLease.request_cancel

    def paused_open_turn(*args, **kwargs):
        accepted.set()
        assert release_accept.wait(timeout=5)
        return real_open_turn(*args, **kwargs)

    def paused_request_cancel(lease, requested_at):
        cancel_read.set()
        assert release_cancel.wait(timeout=5)
        return real_request_cancel(lease, requested_at)

    monkeypatch.setattr(direct_module, "open_turn", paused_open_turn)
    monkeypatch.setattr(TurnLease, "request_cancel", paused_request_cancel)
    worker = threading.Thread(
        target=lambda: service.send_message(sid, workspace, "cancel/open ordering"),
        daemon=True,
    )
    worker.start()
    assert accepted.wait(timeout=2)
    lease = runtime.handle.active_lease
    assert lease.state == "CLAIMING"
    cancel_thread = threading.Thread(target=service.cancel, args=(sid, workspace), daemon=True)
    cancel_thread.start()
    assert cancel_read.wait(timeout=2)

    if open_first:
        release_accept.set()
        worker.join(timeout=2)
        assert lease.state == "OPEN"
        release_cancel.set()
    else:
        release_cancel.set()
        _wait(lambda: lease.requested_at is not None, timeout=2)
        assert lease.state == "CLAIMING"
        release_accept.set()
        worker.join(timeout=2)
    cancel_thread.join(timeout=2)
    worker.join(timeout=2)

    assert not worker.is_alive() and not cancel_thread.is_alive()
    assert runtime.handle.active_lease is lease
    assert runtime.turn_owner is lease
    assert lease.state == "OPEN" and lease.cancel_epoch == 1
    assert lease.requested_at is not None
    envelope = runtime.handle._queue.get_nowait()
    assert isinstance(envelope, QueuedPrompt) and envelope.lease is lease
    assert not hasattr(runtime.handle, "pending_cancel")
    assert not hasattr(runtime, "turn_admission")
    assert not hasattr(runtime, "turn_watchdog")

    note_turn_closed(runtime)
    successor = begin_turn_admission(runtime)
    assert successor is not lease and successor.cancel_epoch == 0
    assert successor.requested_at is None
    successor.abort()


@pytest.mark.parametrize("queued", [False, True], ids=["direct", "queued-delivery"])
def test_accept_wedged_through_real_cancel_grace_never_starts_rpc(
    admission, monkeypatch, queued
) -> None:
    service, db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime, cancel_grace=0.05)
    accepted, release_accept = threading.Event(), threading.Event()
    target_module = queued_module if queued else direct_module
    real_open_turn = target_module.open_turn

    def paused_open_turn(*args, **kwargs):
        accepted.set()
        assert release_accept.wait(timeout=5)
        return real_open_turn(*args, **kwargs)

    monkeypatch.setattr(target_module, "open_turn", paused_open_turn)
    message = None
    errors = []

    def direct_send() -> None:
        try:
            service.send_message(sid, workspace, "wedged accept")
        except ConflictError as exc:
            errors.append(str(exc))

    if queued:
        assert wake_session(service, sid, runtime, ["finished-task"])
        message = service.send_message(sid, workspace, "wedged queued accept")
        runtime.handle._queue.get_nowait()
        queued_prompt = runtime.handle._queue.get_nowait()
        service._events().on_turn_end(sid, "end_turn")
        worker = threading.Thread(target=queued_prompt.before_start, daemon=True)
    else:
        worker = threading.Thread(target=direct_send, daemon=True)
    worker.start()
    assert accepted.wait(timeout=2)
    lease = runtime.handle.active_lease
    assert lease.state == "CLAIMING"
    runtime.handle.rpc_mutex.acquire()
    cancel_thread = threading.Thread(target=service.cancel, args=(sid, workspace), daemon=True)
    cancel_thread.start()
    try:
        _wait(lambda: process.killed.is_set() and lease.state == "KILLING", timeout=2)
        assert not runtime.turn_open
        assert lease.cancel_epoch == 1
        assert lease.cleanup_thread.is_alive()
        assert runtime.handle._queue.empty()

        release_accept.set()
        worker.join(timeout=2)
        cancel_thread.join(timeout=2)
        assert not worker.is_alive() and not cancel_thread.is_alive()
        assert len(errors) == (0 if queued else 1)
        assert runtime.handle._queue.empty()
        assert lease.state == "KILLING"
    finally:
        release_accept.set()
        worker.join(timeout=2)
        cancel_thread.join(timeout=2)
        runtime.handle.rpc_mutex.release()

    conn = SimpleNamespace(
        prompt=Mock(
            side_effect=lambda *_args: asyncio.sleep(
                0, result=SimpleNamespace(stop_reason="end_turn")
            )
        ),
        cancel=AsyncMock(),
    )
    result = asyncio.run(
        runtime.handle._prompt_start_gate.run(lease, lambda: conn.prompt("acp-session", []))
    )
    assert result is None and conn.prompt.call_count == 0
    lease.cleanup_thread.join(timeout=2)
    _wait(
        lambda: (
            service.runtime(sid) is None and db.get_studio_chat_session(sid)["status"] == "error"
        )
    )
    if message is None:
        message = db.list_studio_chat_messages(sid)[0]
    unknown = [
        event
        for event in _events(service, sid, workspace, "error")
        if event.get("message_id") == message["id"] and event.get("outcome") == "unknown"
    ]
    assert len(unknown) == 1
    assert process.kill_calls == 1 and lease.state == "FATAL"
    assert begin_turn_admission(runtime) is None


def test_claim_rejection_aborts_without_killing_or_poisoning_successor(admission) -> None:
    _service, _db, _sid, _workspace, runtime = admission
    process = _install(_service, _sid, runtime, cancel_grace=5)

    assert claim_turn_lease(runtime, lambda: False) is None
    rejected = runtime.handle.active_lease
    assert rejected.state == "ABORTED"
    assert process.kill_calls == 0 and runtime.handle.fatal_lease is None

    successor = begin_turn_admission(runtime)
    assert successor is not rejected and successor.cancel_epoch == 0
    successor.abort()


def test_proven_unclaimed_exception_aborts_and_restores_previous_owner(admission) -> None:
    service, _db, sid, _workspace, runtime = admission
    process = _install(service, sid, runtime, cancel_grace=5)
    previous_owner = runtime.turn_owner = object()

    def fail_claim():
        raise RuntimeError("claim failed")

    with pytest.raises(RuntimeError, match="claim failed"):
        claim_turn_lease(runtime, fail_claim, claim_status=lambda: False)
    rejected = runtime.handle.active_lease
    assert rejected.state == "ABORTED"
    assert runtime.turn_owner is previous_owner
    assert process.kill_calls == 0 and runtime.handle.fatal_lease is None

    successor = begin_turn_admission(runtime)
    assert successor is not rejected and successor.cancel_epoch == 0
    successor.abort()


def test_claim_failure_after_killing_cannot_revoke_fatal_lease(admission, monkeypatch) -> None:
    service, _db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime, cancel_grace=0.05)
    claim_entered, release_claim = threading.Event(), threading.Event()
    result = []
    runtime.handle.rpc_mutex.acquire()

    def claim() -> bool:
        claim_entered.set()
        assert release_claim.wait(timeout=5)
        return False

    worker = threading.Thread(
        target=lambda: result.append(claim_turn_lease(runtime, claim)), daemon=True
    )
    worker.start()
    assert claim_entered.wait(timeout=2)
    lease = runtime.handle.active_lease
    monkeypatch.setattr(lease, "_escalate", Mock())
    try:
        service.cancel(sid, workspace)
        _wait(lambda: process.killed.is_set() and lease.state == "KILLING", timeout=2)
        release_claim.set()
        worker.join(timeout=2)
        assert result == [None]
        assert lease.state == "KILLING" and runtime.handle.fatal_lease is lease
    finally:
        release_claim.set()
        worker.join(timeout=2)
        runtime.handle.rpc_mutex.release()

    lease.cleanup_thread.join(timeout=2)
    assert lease.state == "FATAL" and process.kill_calls == 1
    assert begin_turn_admission(runtime) is None


def test_empty_retry_accept_cancel_carries_the_new_lease(admission, monkeypatch) -> None:
    service, db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime, cancel_grace=5)
    message = service.send_message(sid, workspace, "lost question")
    original = runtime.handle._queue.get_nowait()
    service._events().on_turn_end(sid, "end_turn")
    if runtime.empty_turn_timer is not None:
        runtime.empty_turn_timer.cancel()
    runtime.empty_turn_retry = (message["id"], "lost question", original.text)
    accepted, release_accept = threading.Event(), threading.Event()
    real_open_turn = retry_module.open_turn

    def paused_open_turn(*args, **kwargs):
        accepted.set()
        assert release_accept.wait(timeout=5)
        return real_open_turn(*args, **kwargs)

    monkeypatch.setattr(retry_module, "open_turn", paused_open_turn)
    outcomes = []
    worker = threading.Thread(
        target=lambda: outcomes.append(retry_module.retry_empty_turn(service, sid, runtime)),
        daemon=True,
    )
    worker.start()
    assert accepted.wait(timeout=2)
    lease = runtime.handle.active_lease
    assert lease.state == "CLAIMING"
    cancel_thread = threading.Thread(target=service.cancel, args=(sid, workspace), daemon=True)
    cancel_thread.start()
    _wait(lambda: lease.requested_at is not None, timeout=2)
    release_accept.set()
    worker.join(timeout=2)
    cancel_thread.join(timeout=2)

    assert outcomes == [True]
    envelope = runtime.handle._queue.get_nowait()
    assert isinstance(envelope, QueuedPrompt) and envelope.lease is lease
    assert lease.cancel_epoch == 1 and runtime.empty_turn_retry is None
    lease.deadline = 0
    lease._fire(lease.generation, lease.reason, lease.cancel_epoch)
    lease.cleanup_thread.join(timeout=2)
    _wait(
        lambda: (
            service.runtime(sid) is None and db.get_studio_chat_session(sid)["status"] == "error"
        )
    )

    unknown = [
        event
        for event in _events(service, sid, workspace, "error")
        if event.get("message_id") == message["id"] and event.get("outcome") == "unknown"
    ]
    assert len(unknown) == 1
    assert process.kill_calls == 1
    assert db.count_studio_chat_user_messages(sid) == 1
