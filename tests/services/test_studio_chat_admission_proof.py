"""Admission recovery must prove this attempt, not a stale or foreign row."""

from __future__ import annotations

import threading

import pytest

from server.app.studio_chat import empty_turn_retry as retry_module
from server.app.studio_chat.acp_session import _CLOSE
from server.app.studio_chat.background_wakeup import wake_session
from server.app.studio_chat.callbacks import ServiceCallbacks
from server.app.studio_chat.restart_settlement import reconcile_zombie_session
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


def _unknowns(db, sid: str, message_id: str) -> list[dict]:
    return [
        event
        for event in _events(db, sid, "error")
        if event.get("message_id") == message_id and event.get("outcome") == "unknown"
    ]


def _prepare_queued(service, sid: str, workspace: str, runtime):
    assert wake_session(service, sid, runtime, ["finished-task"])
    message = service.send_message(sid, workspace, "queued proof turn")
    runtime.handle._queue.get_nowait()
    envelope = runtime.handle._queue.get_nowait()
    service._events().on_turn_end(sid, "end_turn")
    if runtime.empty_turn_timer is not None:
        runtime.empty_turn_timer.cancel()
    return message, envelope


def _prepare_empty(service, sid: str, workspace: str, runtime):
    message = service.send_message(sid, workspace, "repeat empty retry")
    original = runtime.handle._queue.get_nowait()
    service._events().on_turn_end(sid, "end_turn")
    if runtime.empty_turn_timer is not None:
        runtime.empty_turn_timer.cancel()
    runtime.empty_turn_retry = (message["id"], "repeat empty retry", original.text)
    return message


def _rearm_empty(service, sid: str, runtime, message, prompt: str) -> None:
    service._events().on_turn_end(sid, "end_turn")
    if runtime.empty_turn_timer is not None:
        runtime.empty_turn_timer.cancel()
    runtime.empty_turn_retry = (message["id"], "repeat empty retry", prompt)


def test_second_empty_retry_cannot_reuse_the_first_attempt_proof(admission, monkeypatch) -> None:
    service, db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime)
    message = _prepare_empty(service, sid, workspace, runtime)
    assert retry_module.retry_empty_turn(service, sid, runtime)
    first_envelope = runtime.handle._queue.get_nowait()
    _rearm_empty(service, sid, runtime, message, first_envelope.text)
    assert len(_events(db, sid, "empty_turn_retry")) == 1

    def fail_before_claim(*_args, **_kwargs):
        raise RuntimeError("second attempt failed before SQL")

    monkeypatch.setattr(db, "claim_studio_chat_turn_with_token", fail_before_claim)
    with pytest.raises(RuntimeError, match="second attempt failed before SQL"):
        retry_module.retry_empty_turn(service, sid, runtime)

    lease = runtime.handle.active_lease
    assert lease.state == "ABORTED" and not runtime.turn_open
    assert runtime.handle._queue.empty()
    assert len(_events(db, sid, "empty_turn_retry")) == 1
    assert db.get_studio_chat_session(sid)["status"] == "idle"
    assert process.kill_calls == 0


def test_foreign_running_row_cannot_admit_background_turn(admission, monkeypatch) -> None:
    service, db, sid, _workspace, runtime = admission
    process = _install(service, sid, runtime)
    db.update_studio_chat_session(sid, status="running")

    def fail_before_claim(*_args, **_kwargs):
        raise RuntimeError("foreign running row is not this attempt")

    monkeypatch.setattr(db, "claim_studio_chat_turn", fail_before_claim)
    with pytest.raises(RuntimeError, match="foreign running row is not this attempt"):
        wake_session(service, sid, runtime, ["finished-task"])

    lease = runtime.handle.active_lease
    lease.cleanup_thread.join(timeout=5)
    _wait(lambda: db.get_studio_chat_session(sid)["status"] == "error")
    assert lease.state == "FATAL" and process.kill_calls == 1
    assert not runtime.turn_open
    remaining = []
    while not runtime.handle._queue.empty():
        remaining.append(runtime.handle._queue.get_nowait())
    assert all(item is _CLOSE for item in remaining)


def test_background_post_commit_without_attempt_proof_is_fatal_not_replayed(
    admission, monkeypatch
) -> None:
    service, db, sid, _workspace, runtime = admission
    process = _install(service, sid, runtime)
    real_claim = db.claim_studio_chat_turn

    def committed_then_error(*args, **kwargs):
        real_claim(*args, **kwargs)
        raise RuntimeError("background post-commit uncertainty")

    monkeypatch.setattr(db, "claim_studio_chat_turn", committed_then_error)
    with pytest.raises(RuntimeError, match="background post-commit uncertainty"):
        wake_session(service, sid, runtime, ["finished-task"])

    lease = runtime.handle.active_lease
    lease.cleanup_thread.join(timeout=5)
    _wait(lambda: db.get_studio_chat_session(sid)["status"] == "error")
    assert lease.state == "FATAL" and process.kill_calls == 1
    assert not runtime.turn_open
    remaining = []
    while not runtime.handle._queue.empty():
        remaining.append(runtime.handle._queue.get_nowait())
    assert all(item is _CLOSE for item in remaining)
    assert reconcile_zombie_session(service, db.get_studio_chat_session(sid))
    assert db.count_studio_chat_user_messages(sid) == 0


def test_second_queued_attempt_cannot_reuse_old_delivery_proof(admission, monkeypatch) -> None:
    service, db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime)
    message, envelope = _prepare_queued(service, sid, workspace, runtime)
    assert envelope.before_start()
    service._events().on_turn_end(sid, "end_turn")
    assert len(_events(db, sid, "queued_delivered")) == 1

    def fail_before_claim(*_args, **_kwargs):
        raise RuntimeError("second queued attempt failed before SQL")

    monkeypatch.setattr(db, "claim_studio_chat_turn_with_token", fail_before_claim)
    assert envelope.before_start() is False

    lease = runtime.handle.active_lease
    assert lease.state == "ABORTED" and not runtime.turn_open
    assert len(_events(db, sid, "queued_delivered")) == 1
    assert len(_events(db, sid, "queued_dropped")) == 1
    assert db.get_studio_chat_session(sid)["status"] == "idle"
    assert process.kill_calls == 0


def test_direct_proof_uses_post_baseline_message_not_same_text_history(
    admission, monkeypatch
) -> None:
    service, db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime)
    first = service.send_message(sid, workspace, "same text")
    runtime.handle._queue.get_nowait()
    service._events().on_turn_end(sid, "end_turn")
    real_accept = db.accept_studio_chat_message

    def committed_then_error(*args, **kwargs):
        real_accept(*args, **kwargs)
        raise RuntimeError("direct post-commit uncertainty")

    monkeypatch.setattr(db, "accept_studio_chat_message", committed_then_error)
    second = service.send_message(sid, workspace, "same text")

    assert second["id"] != first["id"]
    assert runtime.turn_retry_source[0] == second["id"]
    assert db.count_studio_chat_user_messages(sid) == 2
    lease = runtime.handle.active_lease
    lease.fail_claim_uncertain("verify direct proof settlement")
    lease.cleanup_thread.join(timeout=5)
    _wait(lambda: db.get_studio_chat_session(sid)["status"] == "error")
    assert len(_unknowns(db, sid, second["id"])) == 1
    assert _unknowns(db, sid, first["id"]) == []
    assert reconcile_zombie_session(service, db.get_studio_chat_session(sid))
    assert len(_unknowns(db, sid, second["id"])) == 1
    assert process.kill_calls == 1


def test_queued_completion_proof_is_scoped_to_the_current_delivery(admission) -> None:
    service, db, sid, workspace, runtime = admission
    process = _install(service, sid, runtime)
    message, envelope = _prepare_queued(service, sid, workspace, runtime)
    assert envelope.before_start()
    service._events().on_turn_end(sid, "end_turn")

    lease = envelope.before_start()
    assert lease.delivery_seq > 0
    lease.fail_claim_uncertain("verify queued completion attempt")
    lease.cleanup_thread.join(timeout=5)
    _wait(lambda: db.get_studio_chat_session(sid)["status"] == "error")

    assert len(_events(db, sid, "queued_delivered")) == 2
    assert len(_unknowns(db, sid, message["id"])) == 1
    assert reconcile_zombie_session(service, db.get_studio_chat_session(sid))
    assert len(_unknowns(db, sid, message["id"])) == 1
    assert process.kill_calls == 1
