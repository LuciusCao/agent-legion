"""Normal terminal versus KILLING arbitration and durable append recovery."""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from acp import RequestError

from server.app.studio_chat import acp_consumer
from server.app.studio_chat.acp_session import _CLOSE
from server.app.studio_chat.background_wakeup import wake_session
from server.app.studio_chat.callbacks import ServiceCallbacks
from server.app.studio_chat.process_exit import AcpConnectionLost
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


def _install(service, sid: str, runtime) -> _FakeProcess:
    process = _FakeProcess()
    runtime.handle.timing = StudioTimingConfig(
        prompt_timeout_seconds=30, cancel_grace_seconds=5, close_grace_seconds=0.05
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


def _normal_events(db, sid: str, message_id: str, outcome: str) -> list[dict]:
    if outcome == "response":
        return [
            event for event in _events(db, sid, "turn_end") if event.get("message_id") == message_id
        ]
    return [
        event
        for event in _events(db, sid, "error")
        if event.get("message_id") == message_id and event.get("outcome") == "failed"
    ]


def _unknowns(db, sid: str, message_id: str) -> list[dict]:
    return [
        event
        for event in _events(db, sid, "error")
        if event.get("message_id") == message_id and event.get("outcome") == "unknown"
    ]


def _prepare_queued(service, sid: str, workspace: str, runtime):
    assert wake_session(service, sid, runtime, ["finished-task"])
    message = service.send_message(sid, workspace, "queued terminal turn")
    runtime.handle._queue.get_nowait()
    envelope = runtime.handle._queue.get_nowait()
    service._events().on_turn_end(sid, "end_turn")
    if runtime.empty_turn_timer is not None:
        runtime.empty_turn_timer.cancel()
    runtime.handle._queue.put(envelope)
    return message


def _fire(lease) -> None:
    lease.request_cancel(time.monotonic())
    lease.deadline = 0
    lease._fire(lease.generation, lease.reason, lease.cancel_epoch)


@pytest.mark.parametrize("queued", [False, True], ids=["direct", "queued"])
@pytest.mark.parametrize("outcome", ["response", "error"])
@pytest.mark.parametrize("stage", ["consumer", "callback", "append"])
def test_normal_terminal_and_killing_have_one_owner(
    admission, monkeypatch, queued: bool, outcome: str, stage: str
) -> None:
    service, db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime)
    if queued:
        message = _prepare_queued(service, sid, workspace, runtime)
    else:
        message = service.send_message(sid, workspace, "direct terminal turn")
    runtime.handle._queue.put(_CLOSE)
    settlement_started, release_settlement = threading.Event(), threading.Event()

    def paused_settlement(_reason, real_escalate):
        settlement_started.set()
        assert release_settlement.wait(timeout=5)
        real_escalate(_reason)

    async def immediate_response():
        return SimpleNamespace(stop_reason="end_turn")

    async def immediate_error():
        raise RequestError(-32000, "ordinary refusal")

    leases = []

    def prompt(*_args):
        lease = runtime.handle.active_lease
        leases.append(lease)
        real_escalate = lease._escalate
        monkeypatch.setattr(
            lease, "_escalate", lambda reason: paused_settlement(reason, real_escalate)
        )
        return immediate_response() if outcome == "response" else immediate_error()

    conn = SimpleNamespace(prompt=Mock(side_effect=prompt), cancel=AsyncMock())
    callback_observations = []
    if stage == "consumer":
        real_prompt_fatal = acp_consumer.prompt_fatal
        fired = []

        def fatal_then_fire(handle, lease):
            result = real_prompt_fatal(handle, lease)
            if not result and not fired:
                fired.append(True)
                _fire(lease or runtime.handle.active_lease)
            return result

        monkeypatch.setattr(acp_consumer, "prompt_fatal", fatal_then_fire)
    elif stage == "callback":
        if outcome == "response":
            real_callback = service._on_turn_end

            def callback_then_fire(*args, **kwargs):
                _fire(runtime.handle.active_lease)
                callback_observations.append(
                    (db.get_studio_chat_session(sid)["status"], settlement_started.is_set())
                )
                return real_callback(*args, **kwargs)

            monkeypatch.setattr(service, "_on_turn_end", callback_then_fire)
        else:
            real_callback = service._on_error

            def callback_then_fire(session_id, detail, *, fatal):
                if not fatal:
                    _fire(runtime.handle.active_lease)
                    callback_observations.append(
                        (db.get_studio_chat_session(sid)["status"], settlement_started.is_set())
                    )
                return real_callback(session_id, detail, fatal=fatal)

            monkeypatch.setattr(service, "_on_error", callback_then_fire)
    else:
        real_append = service.store.append_message

        def append_then_fire(session_id, kind, role, content):
            result = real_append(session_id, kind, role, content)
            if content.get("message_id") == message["id"] and (
                (outcome == "response" and content.get("event") == "turn_end")
                or (outcome == "error" and content.get("outcome") == "failed")
            ):
                _fire(runtime.handle.active_lease)
            return result

        monkeypatch.setattr(service.store, "append_message", append_then_fire)

    try:
        with contextlib.suppress(AcpConnectionLost):
            asyncio.run(runtime.handle._prompt_loop(conn, "acp-session"))
    finally:
        release_settlement.set()
        if leases and leases[0].cleanup_thread is not None:
            leases[0].cleanup_thread.join(timeout=5)

    normal = _normal_events(db, sid, message["id"], outcome)
    unknown = _unknowns(db, sid, message["id"])
    if stage == "append":
        assert len(normal) == 1 and unknown == []
        assert process.kill_calls == 0
        assert db.get_studio_chat_session(sid)["status"] == "idle"
        assert leases[0].state == "CLOSED"
    else:
        assert leases[0].state == "FATAL"
        assert normal == [] and len(unknown) == 1
        assert process.kill_calls == 1
        assert db.get_studio_chat_session(sid)["status"] == "error"
        if stage == "callback":
            assert callback_observations == [("running", True)]


@pytest.mark.parametrize("outcome", ["response", "error"])
def test_post_commit_normal_append_exception_uses_its_durable_proof(
    admission, monkeypatch, outcome: str
) -> None:
    service, db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime)
    message = service.send_message(sid, workspace, "post-commit terminal append")
    runtime.handle._queue.put(_CLOSE)

    async def immediate_response():
        return SimpleNamespace(stop_reason="end_turn")

    async def immediate_error():
        raise RequestError(-32000, "ordinary refusal")

    conn = SimpleNamespace(
        prompt=Mock(
            side_effect=lambda *_args: (
                immediate_response() if outcome == "response" else immediate_error()
            )
        ),
        cancel=AsyncMock(),
    )
    real_append = service.store.append_message

    def append_then_error(session_id, kind, role, content):
        result = real_append(session_id, kind, role, content)
        if content.get("message_id") == message["id"] and (
            (outcome == "response" and content.get("event") == "turn_end")
            or (outcome == "error" and content.get("outcome") == "failed")
        ):
            raise RuntimeError("post-commit terminal append uncertainty")
        return result

    monkeypatch.setattr(service.store, "append_message", append_then_error)
    with contextlib.suppress(AcpConnectionLost):
        asyncio.run(runtime.handle._prompt_loop(conn, "acp-session"))

    assert len(_normal_events(db, sid, message["id"], outcome)) == 1
    assert _unknowns(db, sid, message["id"]) == []
    assert process.kill_calls == 0
    assert db.get_studio_chat_session(sid)["status"] == "idle"


def test_normal_completion_dominates_fatal_when_proof_read_fails(admission, monkeypatch) -> None:
    service, db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime)
    message = service.send_message(sid, workspace, "terminal proof read failure")
    runtime.handle._queue.put(_CLOSE)

    async def immediate_response():
        return SimpleNamespace(stop_reason="end_turn")

    conn = SimpleNamespace(
        prompt=Mock(side_effect=lambda *_args: immediate_response()), cancel=AsyncMock()
    )
    real_append = service.store.append_message

    def append_then_error(session_id, kind, role, content):
        result = real_append(session_id, kind, role, content)
        if content.get("message_id") == message["id"] and content.get("event") == "turn_end":
            raise RuntimeError("post-commit terminal append uncertainty")
        return result

    def proof_read_error(*_args, **_kwargs):
        raise RuntimeError("terminal proof read failed")

    monkeypatch.setattr(service.store, "append_message", append_then_error)
    monkeypatch.setattr(db, "list_studio_chat_messages_forward", proof_read_error)
    with contextlib.suppress(AcpConnectionLost):
        asyncio.run(runtime.handle._prompt_loop(conn, "acp-session"))
    lease = runtime.handle.active_lease
    if lease.cleanup_thread is not None:
        lease.cleanup_thread.join(timeout=5)
    _wait(lambda: db.get_studio_chat_session(sid)["status"] == "error")

    assert len(_normal_events(db, sid, message["id"], "response")) == 1
    assert _unknowns(db, sid, message["id"]) == []
    assert process.kill_calls == 1
    assert message["id"] not in runtime.inbound_messages


@pytest.mark.parametrize("outcome", ["response", "error"])
def test_append_failure_rolls_back_normal_close_ownership(
    admission, monkeypatch, outcome: str
) -> None:
    service, db, sid, workspace, runtime = admission
    _process = _install(service, sid, runtime)
    message = service.send_message(sid, workspace, "append failure rollback")
    runtime.handle._queue.put(_CLOSE)

    async def immediate_response():
        return SimpleNamespace(stop_reason="end_turn")

    async def immediate_error():
        raise RequestError(-32000, "ordinary refusal")

    conn = SimpleNamespace(
        prompt=Mock(
            side_effect=lambda *_args: (
                immediate_response() if outcome == "response" else immediate_error()
            )
        ),
        cancel=AsyncMock(),
    )

    def append_error(*_args, **_kwargs):
        raise RuntimeError("append failed before commit")

    monkeypatch.setattr(service.store, "append_message", append_error)
    with pytest.raises(RuntimeError, match="append failed before commit"):
        asyncio.run(runtime.handle._prompt_loop(conn, "acp-session"))
    lease = runtime.handle.active_lease

    assert lease.state != "CLOSED" and runtime.turn_open
    assert runtime.inbound_messages.get(message["id"]) == "delivered"
    monkeypatch.undo()
    assert lease.request_cancel(time.monotonic())
    lease.fail_claim_uncertain("rollback verification")
    lease.cleanup_thread.join(timeout=5)
    _wait(lambda: db.get_studio_chat_session(sid)["status"] == "error")
    assert len(_unknowns(db, sid, message["id"])) == 1


@pytest.mark.parametrize("outcome", ["response", "error"])
def test_finalization_failure_after_durable_terminal_does_not_double_settle(
    admission, monkeypatch, outcome: str
) -> None:
    service, db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime)
    message = service.send_message(sid, workspace, "terminal finalization failure")
    runtime.handle._queue.put(_CLOSE)

    async def immediate_response():
        return SimpleNamespace(stop_reason="end_turn")

    async def immediate_error():
        raise RequestError(-32000, "ordinary refusal")

    conn = SimpleNamespace(
        prompt=Mock(
            side_effect=lambda *_args: (
                immediate_response() if outcome == "response" else immediate_error()
            )
        ),
        cancel=AsyncMock(),
    )
    real_update = db.update_studio_chat_session_if

    def fail_idle_update(*args, **kwargs):
        if kwargs.get("status") == "idle":
            raise RuntimeError("idle update failed after terminal append")
        return real_update(*args, **kwargs)

    monkeypatch.setattr(db, "update_studio_chat_session_if", fail_idle_update)
    with contextlib.suppress(AcpConnectionLost):
        asyncio.run(runtime.handle._prompt_loop(conn, "acp-session"))
    lease = runtime.handle.active_lease
    if lease.cleanup_thread is not None:
        lease.cleanup_thread.join(timeout=5)
    _wait(lambda: db.get_studio_chat_session(sid)["status"] == "error")

    assert len(_normal_events(db, sid, message["id"], outcome)) == 1
    assert _unknowns(db, sid, message["id"]) == []
    assert message["id"] not in runtime.inbound_messages
    assert process.kill_calls == 1
