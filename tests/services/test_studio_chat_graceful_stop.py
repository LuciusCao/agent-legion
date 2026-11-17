"""Graceful stop must not turn an in-flight normal terminal into unknown."""

from __future__ import annotations

import asyncio
import contextlib
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from acp import RequestError

from server.app.auth.sessions import hash_token
from server.app.studio_chat import acp_consumer
from server.app.studio_chat.callbacks import ServiceCallbacks
from server.app.studio_chat.process_exit import AcpConnectionLost
from server.app.studio_chat.prompt_admission import QueuedPrompt
from server.app.studio_chat.restart_settlement import reconcile_zombie_session
from server.app.studio_chat.resume_context import build_resume_transcript
from server.app.studio_chat.timing import StudioTimingConfig
from server.app.studio_chat.token_keepalive import keepalive_run_token
from tests.helpers import studio_chat_fixtures as fixtures

admission = fixtures.admission
TOKEN_DEAD_DETAIL = "run token invalidated: tool channel unrecoverable (#558)"


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


def _consume(runtime, conn) -> None:
    with contextlib.suppress(AcpConnectionLost):
        asyncio.run(runtime.handle._prompt_loop(conn, "acp-session"))


@pytest.mark.parametrize("outcome", ["response", "error"])
def test_dead_token_graceful_stop_preserves_inflight_normal_terminal(
    admission, outcome: str
) -> None:
    service, db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime)
    message = service.send_message(sid, workspace, "complete before graceful stop")
    db.revoke_scoped_token(hash_token(runtime.token))
    keepalive_run_token(service, sid)

    assert runtime.handle._stop_requested
    assert db.get_studio_chat_session(sid)["error_detail"] == TOKEN_DEAD_DETAIL
    assert not runtime.handle.send_prompt("must not start after token death")

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
    _consume(runtime, conn)
    runtime.handle.callbacks.on_exit(close_initiated=False)

    assert len(_normal_events(db, sid, message["id"], outcome)) == 1
    assert _unknowns(db, sid, message["id"]) == []
    assert db.get_studio_chat_session(sid)["status"] == "error"
    assert db.get_studio_chat_session(sid)["error_detail"] == TOKEN_DEAD_DETAIL
    assert process.kill_calls == 0
    assert reconcile_zombie_session(service, db.get_studio_chat_session(sid))
    assert _unknowns(db, sid, message["id"]) == []
    transcript = build_resume_transcript(db.list_studio_chat_messages(sid))
    assert "complete before graceful stop" in transcript


@pytest.mark.parametrize("outcome", ["response", "error"])
def test_fatal_stop_still_uses_unknown(admission, outcome: str) -> None:
    service, db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime)
    message = service.send_message(sid, workspace, "fatal stop must stay unknown")
    lease = runtime.handle.active_lease
    lease.fail_claim_uncertain("fatal before normal terminal")

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
    _consume(runtime, conn)
    lease.cleanup_thread.join(timeout=5)

    assert lease.state == "FATAL" and process.kill_calls == 1
    assert _normal_events(db, sid, message["id"], outcome) == []
    assert len(_unknowns(db, sid, message["id"])) == 1
    assert db.get_studio_chat_session(sid)["status"] == "error"


@pytest.mark.parametrize("queued", [False, True], ids=["direct", "bound-queued"])
@pytest.mark.parametrize("outcome", ["response", "error"])
def test_stop_between_admission_snapshot_and_start_still_finishes_accepted_lease(
    admission, monkeypatch, queued: bool, outcome: str
) -> None:
    service, db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime)
    message = service.send_message(sid, workspace, "accepted before snapshot stop")
    if queued:
        accepted = runtime.handle._queue.get_nowait()
        assert accepted.lease is runtime.handle.active_lease
        runtime.handle._queue.put(
            QueuedPrompt(
                accepted.text,
                accepted.lease,
                before_start=Mock(return_value=accepted.lease),
            )
        )

    db.revoke_scoped_token(hash_token(runtime.token))
    keepalive_run_token(service, sid)
    monkeypatch.setattr(acp_consumer, "prompt_admission_open", lambda _handle: True)

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
    _consume(runtime, conn)
    runtime.handle.callbacks.on_exit(close_initiated=False)

    assert runtime.handle._stop_requested
    assert conn.prompt.call_count == 1
    assert len(_normal_events(db, sid, message["id"], outcome)) == 1
    assert _unknowns(db, sid, message["id"]) == []
    assert db.get_studio_chat_session(sid)["error_detail"] == TOKEN_DEAD_DETAIL
    assert not runtime.handle.send_prompt("new admission must stay fenced")
    assert process.kill_calls == 0
    assert reconcile_zombie_session(service, db.get_studio_chat_session(sid))
    assert _unknowns(db, sid, message["id"]) == []
