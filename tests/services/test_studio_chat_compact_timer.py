"""Compaction timer recovery from transient persistence failures (#1153)."""

from __future__ import annotations

from server.app.studio_chat import compact_timer, compact_timer_fire, compaction
from tests.helpers import wait_for_predicate
from tests.services import test_studio_chat_compaction as compact_cases

_chunk = compact_cases._chunk
_ready = compact_cases._ready
_status_events = compact_cases._status_events
direct = compact_cases.direct


def test_timer_clear_db_failure_retries_and_recovers(direct, monkeypatch) -> None:
    service, _bus, session_id, runtime, workspace_id = direct
    _ready(service, session_id)
    service._on_update(session_id, _chunk("Compacting conversation context\n"))
    with runtime.lock:
        armed_since = runtime.compacting_since
    real_clear = service._db.clear_studio_chat_compacting_if_set
    calls = 0

    def fail_once(sid):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("injected transient compact clear failure")
        return real_clear(sid)

    monkeypatch.setattr(service._db, "clear_studio_chat_compacting_if_set", fail_once)
    timer_errors = []
    try:
        compact_timer._fire(service, session_id, runtime, armed_since)
    except Exception as exc:
        timer_errors.append(exc)
    assert not timer_errors, {
        "runtime_compacting": runtime.compacting,
        "runtime_since": runtime.compacting_since,
        "runtime_timer": runtime.compact_timer,
        "row_compacting": service.get_session(session_id)["compacting"],
        "errors": timer_errors,
    }

    wait_for_predicate(
        lambda: (
            runtime.compacting is False
            and runtime.compact_timer is None
            and service.get_session(session_id)["compacting"] is False
            and any(
                e["event"] == "compact_timeout"
                for e in _status_events(service, session_id, workspace_id)
            )
        ),
        timeout=30,
    )
    assert calls == 2
    events = [e["event"] for e in _status_events(service, session_id, workspace_id)]
    assert events == ["compact_start", "compact_timeout"]
    service.send_message(session_id, workspace_id, "恢复后的消息")
    assert service.get_session(session_id)["status"] == "running"


def test_clear_retry_exhaustion_keeps_window_and_later_recovers(direct, monkeypatch) -> None:
    service, _bus, session_id, runtime, workspace_id = direct
    monkeypatch.setattr(compact_timer_fire, "_CLEAR_ATTEMPTS", 2)
    _ready(service, session_id)
    service._on_update(session_id, _chunk("Compacting conversation context\n"))
    with runtime.lock:
        armed_since = runtime.compacting_since
    real_clear = service._db.clear_studio_chat_compacting_if_set
    calls = 0

    def always_fail(_sid):
        nonlocal calls
        calls += 1
        raise RuntimeError("persistent compact clear failure")

    monkeypatch.setattr(service._db, "clear_studio_chat_compacting_if_set", always_fail)
    compact_timer._fire(service, session_id, runtime, armed_since)
    wait_for_predicate(lambda: runtime.compact_timer is None and calls == 2, timeout=30)
    assert runtime.compacting is True
    assert runtime.compacting_since == armed_since
    assert service.get_session(session_id)["compacting"] is True
    assert [e["event"] for e in _status_events(service, session_id, workspace_id)] == [
        "compact_start"
    ]

    monkeypatch.setattr(service._db, "clear_studio_chat_compacting_if_set", real_clear)
    compact_timer._fire(service, session_id, runtime, armed_since)
    assert runtime.compacting is False
    assert service.get_session(session_id)["compacting"] is False
    assert [e["event"] for e in _status_events(service, session_id, workspace_id)] == [
        "compact_start",
        "compact_timeout",
    ]
    service.send_message(session_id, workspace_id, "恢复后的消息")
    assert service.get_session(session_id)["status"] == "running"


def test_failed_clear_retry_cannot_clear_rearmed_window(direct, monkeypatch) -> None:
    service, _bus, session_id, runtime, workspace_id = direct
    monkeypatch.setattr(compact_timer_fire, "_CLEAR_RETRY_SECONDS", 10)
    _ready(service, session_id)
    service._on_update(session_id, _chunk("Compacting conversation context\n"))
    with runtime.lock:
        armed_since = runtime.compacting_since
    real_clear = service._db.clear_studio_chat_compacting_if_set
    calls = 0

    def fail_once(sid):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("injected transient compact clear failure")
        return real_clear(sid)

    monkeypatch.setattr(service._db, "clear_studio_chat_compacting_if_set", fail_once)
    compact_timer._fire(service, session_id, runtime, armed_since)
    assert runtime.compacting is True
    monkeypatch.setattr(compaction, "COMPACTING_TIMEOUT_SECONDS", 0.1)
    service._on_update(session_id, _chunk("Compacting conversation context\n"))
    with runtime.lock:
        rearmed_since = runtime.compacting_since
    assert rearmed_since != armed_since

    compact_timer._fire(service, session_id, runtime, armed_since)
    assert calls == 1
    assert runtime.compacting is True
    assert service.get_session(session_id)["compacting"] is True
    assert [e["event"] for e in _status_events(service, session_id, workspace_id)] == [
        "compact_start"
    ]

    wait_for_predicate(
        lambda: (
            runtime.compacting is False
            and service.get_session(session_id)["compacting"] is False
            and any(
                e["event"] == "compact_timeout"
                for e in _status_events(service, session_id, workspace_id)
            )
        ),
        timeout=30,
    )
    assert calls == 2
    assert [e["event"] for e in _status_events(service, session_id, workspace_id)] == [
        "compact_start",
        "compact_timeout",
    ]


def test_timeout_notice_failure_keeps_mirrors_consistent(direct, monkeypatch) -> None:
    service, _bus, session_id, runtime, workspace_id = direct
    _ready(service, session_id)
    service._on_update(session_id, _chunk("Compacting conversation context\n"))
    with runtime.lock:
        armed_since = runtime.compacting_since

    def fail_notice(*_args):
        raise RuntimeError("injected compact notice failure")

    monkeypatch.setattr(service.store, "append_message", fail_notice)
    compact_timer._fire(service, session_id, runtime, armed_since)
    assert runtime.compacting is False
    assert runtime.compact_timer is None
    assert service.get_session(session_id)["compacting"] is False
    assert [e["event"] for e in _status_events(service, session_id, workspace_id)] == [
        "compact_start"
    ]
    service.send_message(session_id, workspace_id, "恢复后的消息")
    assert service.get_session(session_id)["status"] == "running"
