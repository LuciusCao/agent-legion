"""Kimi Code 1.52 structured wire compaction evidence (#1025).

All journals are synthetic protocol-1.10 records with the upstream
``timestamp + message{type,payload}`` shape. No user journal is read.
"""

from __future__ import annotations

import json

import pytest

from server.app.services.job_errors import ConflictError
from server.app.studio_chat import compaction
from server.app.studio_chat.unprompted_queue import GatedUnpromptedWatcher
from tests.helpers import studio_chat_fixtures as fixtures

admission = fixtures.admission

BEGIN = {
    "timestamp": 1795680000.125,
    "message": {"type": "CompactionBegin", "payload": {}},
}
END = {
    "timestamp": 1795680001.25,
    "message": {"type": "CompactionEnd", "payload": {}},
}
TURN_END = {
    "timestamp": 1795680000.75,
    "message": {"type": "TurnEnd", "payload": {}},
}


def _status(used: int, size: int) -> dict:
    return {
        "timestamp": 1795680000.5,
        "message": {
            "type": "StatusUpdate",
            "payload": {
                "context_usage": used / size,
                "context_tokens": used,
                "max_context_tokens": size,
                "token_usage": None,
                "message_id": "msg_synthetic",
                "plan_mode": False,
                "mcp_status": None,
            },
        },
    }


def _chunk(text: str) -> dict:
    return {
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": text},
    }


def _append(path, *records: dict) -> None:
    with path.open("a") as handle:
        handle.writelines(json.dumps(record) + "\n" for record in records)


def _events(db, sid: str) -> list[dict]:
    return [
        message["content"]
        for message in db.list_studio_chat_messages(sid)
        if message["kind"] == "status"
        and message["content"].get("event") in ("compact_start", "compact_done")
    ]


@pytest.fixture
def wire(admission, tmp_path):
    service, db, sid, workspace, runtime = admission
    runtime.kimi_agent = True
    runtime.loading = False
    path = tmp_path / "wire.jsonl"
    path.write_text(json.dumps({"type": "metadata", "protocol_version": "1.10"}) + "\n")
    watcher = GatedUnpromptedWatcher(service, sid, runtime, lambda: path)
    return service, db, sid, workspace, runtime, watcher, path


def test_text_free_begin_status_turn_end_and_end_drive_existing_guard(wire, monkeypatch):
    service, db, sid, workspace, runtime, watcher, path = wire
    # Fault injection: any dependence on the legacy text classifier fails here.
    monkeypatch.setattr(
        compaction, "compact_marker", lambda _text: pytest.fail("wire used text classification")
    )
    db.update_studio_chat_session(sid, usage={"used": 100, "size": 1000, "cost": {"total": 0.25}})
    _append(path, BEGIN)
    # Admission refreshes the watcher before its early guard: no poll is needed.
    with pytest.raises(ConflictError, match="正在压缩上下文"):
        service.send_message(sid, workspace, "must wait")
    assert runtime.compacting is True and runtime.compaction_seen is True
    assert db.get_studio_chat_session(sid)["compacting"] is True
    assert db.get_studio_chat_session(sid)["status"] == "idle"
    assert [event["event"] for event in _events(db, sid)] == ["compact_start"]

    _append(path, _status(31234, 262144), TURN_END)
    watcher.step()
    # TurnEnd is not a compaction outcome and cannot invent cancellation.
    assert runtime.compacting is True
    assert db.get_studio_chat_session(sid)["usage"] == {
        "used": 31234,
        "size": 262144,
        "cost": {"total": 0.25},
    }
    _append(path, END)
    watcher.step()
    assert runtime.compacting is False
    assert db.get_studio_chat_session(sid)["compacting"] is False
    assert [event["event"] for event in _events(db, sid)] == ["compact_start", "compact_done"]
    service.send_message(sid, workspace, "continue")
    assert db.get_studio_chat_session(sid)["status"] == "running"


def test_structured_begin_is_trusted_inside_an_ordinary_turn(wire):
    service, db, sid, workspace, runtime, watcher, path = wire
    service.send_message(sid, workspace, "ordinary model turn")
    assert runtime.turn_open is True and runtime.turn_may_compact is False
    _append(path, BEGIN)
    watcher.step()
    assert runtime.compacting is True
    _append(path, END)
    watcher.step()
    assert runtime.compacting is False
    assert [event["event"] for event in _events(db, sid)] == ["compact_start", "compact_done"]


def test_structured_and_legacy_duplicates_share_one_window_and_two_notices(wire):
    service, db, sid, _workspace, _runtime, watcher, path = wire
    _append(path, BEGIN, BEGIN)
    watcher.step()
    service._on_update(sid, _chunk("Compacting conversation context\n"))
    _append(path, END, END)
    watcher.step()
    service._on_update(sid, _chunk("Compaction completed.\n- Tokens after: 9,999"))
    assert [event["event"] for event in _events(db, sid)] == ["compact_start", "compact_done"]
    assert db.get_studio_chat_session(sid)["usage"] is None


@pytest.mark.parametrize("version", ["1.1", "1.9", "1.11", "2.0"])
def test_unsupported_protocol_versions_stay_neutral_and_continue(wire, version):
    service, db, sid, workspace, runtime, watcher, path = wire
    path.write_text(json.dumps({"type": "metadata", "protocol_version": version}) + "\n")
    _append(path, BEGIN)
    watcher.step()
    assert runtime.compacting is False and runtime.compaction_seen is False
    assert _events(db, sid) == []
    service.send_message(sid, workspace, "ordinary fallback")
    assert db.get_studio_chat_session(sid)["status"] == "running"


def test_malformed_envelopes_cannot_drive_the_guard(wire):
    service, db, sid, workspace, runtime, watcher, path = wire
    malformed = [
        {"message": BEGIN["message"]},
        {**BEGIN, "timestamp": float("nan")},
        {**BEGIN, "message": {"type": "CompactionBegin", "payload": []}},
        {**BEGIN, "message": {"type": "CompactionBegin", "payload": {"cancelled": True}}},
        {"type": "CompactionBegin", "payload": {}},
        {**END, "message": {"type": "TurnEnd", "payload": {"cancelled": True}}},
        {
            "timestamp": 1795680002.0,
            "message": {
                "type": "StatusUpdate",
                "payload": {"context_tokens": True, "max_context_tokens": 1000},
            },
        },
    ]
    _append(path, *malformed)
    watcher.step()
    assert runtime.compacting is False and runtime.compaction_seen is False
    assert db.get_studio_chat_session(sid)["usage"] is None
    assert _events(db, sid) == []
    service.send_message(sid, workspace, "safe fallback")
    assert db.get_studio_chat_session(sid)["status"] == "running"


def test_partial_metadata_is_retried_before_the_first_live_begin(wire):
    _service, db, sid, _workspace, runtime, watcher, path = wire
    path.write_text(json.dumps({"type": "metadata", "protocol_version": "1.10"}))
    watcher.step()
    assert watcher.tail.protocol_version is None
    with path.open("a") as handle:
        handle.write("\n" + json.dumps(BEGIN) + "\n")
    watcher.step()
    assert watcher.tail.protocol_version == "1.10"
    assert runtime.compacting is True
    assert [event["event"] for event in _events(db, sid)] == ["compact_start"]


def test_loaded_baseline_skips_history_but_loads_header_and_sees_live_begin(wire):
    _service, db, sid, _workspace, runtime, watcher, path = wire
    _append(path, BEGIN, END)
    watcher.baseline_on_locate = True
    watcher.step()
    assert watcher.tail.protocol_version == "1.10"
    assert runtime.compacting is False and _events(db, sid) == []
    _append(path, BEGIN)
    watcher.step()
    assert runtime.compacting is True
    assert [event["event"] for event in _events(db, sid)] == ["compact_start"]


@pytest.mark.parametrize("mode", ["truncate", "replace"])
def test_replacement_or_truncation_rebaselines_without_replaying_stale_end(wire, mode):
    _service, db, sid, _workspace, runtime, watcher, path = wire
    _append(path, BEGIN)
    watcher.step()
    assert runtime.compacting is True
    tmp_path = path.with_suffix(".replacement")
    tmp_path.write_text(json.dumps({"type": "metadata", "protocol_version": "1.10"}) + "\n")
    _append(tmp_path, END)
    if mode == "replace":
        tmp_path.replace(path)
    else:
        path.write_text(tmp_path.read_text())
    watcher.step()
    assert runtime.compacting is True
    assert [event["event"] for event in _events(db, sid)] == ["compact_start"]
    watcher.step()  # settled identity: bounded header load, still no history
    assert runtime.compacting is True
    _append(path, END)
    watcher.step()
    assert runtime.compacting is False
    assert [event["event"] for event in _events(db, sid)] == ["compact_start", "compact_done"]


def test_db_backlog_pauses_journal_and_retries_without_losing_or_duplicating(wire, monkeypatch):
    service, db, sid, _workspace, runtime, watcher, path = wire
    append = service.store.append_message
    failing = True

    def flaky_append(session_id, kind, role, content):
        if failing and content.get("event") == "compact_start":
            raise RuntimeError("synthetic DB failure")
        return append(session_id, kind, role, content)

    monkeypatch.setattr(service.store, "append_message", flaky_append)
    _append(path, BEGIN)
    with pytest.raises(RuntimeError, match="synthetic DB failure"):
        watcher.step()
    assert runtime.compacting is True  # conservative guard survives the DB failure
    assert len(watcher.pending) == 1
    _append(path, END)
    offset = watcher.tail.offset
    with pytest.raises(RuntimeError, match="synthetic DB failure"):
        watcher.step()
    assert len(watcher.pending) == 1 and watcher.tail.offset == offset
    assert runtime.compacting is True  # End cannot pass the durable backlog

    failing = False
    watcher.step()
    assert watcher.pending == [] and watcher.tail.offset == offset
    assert [event["event"] for event in _events(db, sid)] == ["compact_start"]
    watcher.step()
    assert runtime.compacting is False
    assert [event["event"] for event in _events(db, sid)] == ["compact_start", "compact_done"]


def test_retried_duplicate_signal_does_not_gain_a_notice(wire, monkeypatch):
    service, db, sid, _workspace, _runtime, watcher, path = wire
    _append(path, BEGIN)
    watcher.step()
    real_update = db.update_studio_chat_session

    def fail_duplicate_mirror(session_id, **fields):
        if fields.get("compacting") is True:
            raise RuntimeError("duplicate mirror failed")
        return real_update(session_id, **fields)

    _append(path, BEGIN)
    with monkeypatch.context() as patch:
        patch.setattr(db, "update_studio_chat_session", fail_duplicate_mirror)
        with pytest.raises(RuntimeError, match="duplicate mirror failed"):
            watcher.step()
    watcher.step()
    assert watcher.pending == []
    assert [event["event"] for event in _events(db, sid)] == ["compact_start"]


def test_wire_begin_between_early_and_late_admission_gates_rolls_back(wire, monkeypatch):
    service, db, sid, workspace, _runtime, watcher, path = wire
    watcher.step()
    real_send_blocked = compaction.send_blocked
    injected = False

    def early_gate_then_wire(db_arg, session_id, runtime, text):
        nonlocal injected
        if not injected:
            injected = True
            _append(path, BEGIN)
            watcher.step()
            return False  # the early gate had already passed in this interleave
        return real_send_blocked(db_arg, session_id, runtime, text)

    monkeypatch.setattr(compaction, "send_blocked", early_gate_then_wire)
    with pytest.raises(ConflictError, match="正在压缩上下文"):
        service.send_message(sid, workspace, "racing")
    assert db.get_studio_chat_session(sid)["status"] == "idle"
    assert db.list_studio_chat_messages(sid)[0]["content"]["event"] == "compact_start"
