"""Agent-launched (unprompted) turns reach the Studio timeline (#938).

Kimi Code 0.43 runs a turn by itself when a background subagent completes or a
cron job fires, while no ``session/prompt`` is in flight; its ACP adapter
drops that turn's events, leaving only the wire journal. The fake ACP agent
models exactly that protocol shape (journal records, zero session/update).
"""

from __future__ import annotations

import json
import os
import time

import pytest

from server.app.studio_chat import unprompted_turns
from server.app.studio_chat.kimi_wire import WireTail, locate_wire
from server.app.studio_chat.unprompted_turns import UnpromptedTurnProjector
from server.app.studio_chat.wire_baseline import WireBaseline, capture_wire_baseline
from tests.helpers import studio_chat_fixtures as fixtures
from tests.helpers import wait_for_predicate

chat = fixtures.chat
admission = fixtures.admission

TASK_TURN = [
    {
        "type": "turn.prompt",
        "agentId": "main",
        "input": [{"type": "text", "text": "<notification ...>"}],
        "origin": {"kind": "task", "taskId": "agent-x1", "status": "completed"},
        "turnId": 1,
    },
    {
        "type": "context.append_loop_event",
        "agentId": "main",
        "event": {"type": "content.part", "turnId": "1", "part": {"type": "think", "think": "hm"}},
    },
    {
        "type": "context.append_loop_event",
        "agentId": "main",
        "event": {
            "type": "tool.call",
            "turnId": "1",
            "toolCallId": "call_9",
            "name": "TaskOutput",
            "args": {"task_id": "agent-x1"},
        },
    },
    {
        "type": "context.append_loop_event",
        "agentId": "main",
        "event": {"type": "tool.result", "toolCallId": "call_9", "result": {"output": "42"}},
    },
    {
        "type": "context.append_loop_event",
        "agentId": "main",
        "event": {
            "type": "content.part",
            "turnId": "1",
            "part": {"type": "text", "text": "REPORT-938: 子代理已完成，结果 42"},
        },
    },
    {"type": "turn.ended", "agentId": "main", "turnId": 1, "reason": "completed"},
]


def _project(records):
    projector = UnpromptedTurnProjector()
    return [row for record in records for row in projector.project(record)]


@pytest.mark.no_db
def test_task_turn_projects_receipt_content_tool_card_and_turn_end():
    rows = _project(TASK_TURN)
    assert [(kind, role) for kind, role, _ in rows] == [
        ("status", "system"),
        ("thought", "agent"),
        ("tool_call", "agent"),
        ("tool_call", "agent"),
        ("text", "agent"),
        ("status", "system"),
    ]
    receipt, thought, call, result, text, end = (content for _, _, content in rows)
    assert receipt["event"] == "unprompted_turn"
    assert receipt["detail"] == "后台任务 agent-x1 已完成，agent 正在汇报"
    assert thought == {"text": "hm"}
    assert call["sessionUpdate"] == "tool_call" and call["toolCallId"] == "1:call_9"
    assert call["title"] == "TaskOutput" and call["rawInput"] == {"task_id": "agent-x1"}
    assert result["sessionUpdate"] == "tool_call_update" and result["toolCallId"] == "1:call_9"
    assert result["status"] == "completed" and result["rawOutput"] == "42"
    assert text == {"text": "REPORT-938: 子代理已完成，结果 42"}
    assert end == {"event": "turn_end", "stop_reason": "end_turn", "unprompted": True}


@pytest.mark.no_db
def test_studio_driven_turns_and_foreign_agents_are_not_projected():
    bound = [
        {**TASK_TURN[0], "origin": {"kind": "user"}, "promptId": "msg_1"},
        *TASK_TURN[1:],
    ]
    assert _project(bound) == []
    skill = [{**TASK_TURN[0], "origin": {"kind": "skill_activation"}}, *TASK_TURN[1:]]
    assert _project(skill) == []
    subagent = [{**record, "agentId": "agent-0"} for record in TASK_TURN]
    assert _project(subagent) == []


@pytest.mark.no_db
def test_cron_fire_receipt_and_cancelled_turn_end():
    rows = _project(
        [
            {**TASK_TURN[0], "origin": {"kind": "cron_job", "jobId": "c1"}, "turnId": 4},
            {"type": "turn.ended", "agentId": "main", "turnId": 4, "reason": "cancelled"},
        ]
    )
    assert rows[0][2]["detail"] == "定时任务已触发，agent 正在处理"
    assert rows[1][2]["stop_reason"] == "cancelled"


@pytest.mark.no_db
def test_failed_turn_is_persisted_as_error_not_turn_end():
    """#938 review R2 P2: ``turn.ended(reason="failed")`` is an error terminal
    (the ACP path's on_turn_error shape), never a completed end_turn; only
    code/message of the payload survive, never details/cause."""
    error = {"code": "PROVIDER_ERROR", "message": "rate limited", "details": {"key": "sk-x"}}
    rows = _project(
        [TASK_TURN[0], {"type": "turn.ended", "agentId": "main", "turnId": 1, "reason": "failed"}]
    )
    assert rows[-1][2] == {
        "event": "error",
        "detail": "agent 自发回合失败",
        "unprompted": True,
    }
    rows = _project(
        [
            TASK_TURN[0],
            {
                "type": "turn.ended",
                "agentId": "main",
                "turnId": 1,
                "reason": "failed",
                "error": error,
            },
        ]
    )
    assert rows[-1][2]["event"] == "error"
    assert rows[-1][2]["detail"] == "agent 自发回合失败：[PROVIDER_ERROR] rate limited"
    assert "sk-x" not in json.dumps(rows, ensure_ascii=False)
    assert all(row[2].get("event") != "turn_end" for row in rows)


@pytest.mark.no_db
def test_failed_tool_result_marks_card_failed():
    records = [*TASK_TURN[:3]]
    records.append(
        {
            **TASK_TURN[3],
            "event": {**TASK_TURN[3]["event"], "result": {"output": "x", "isError": True}},
        }
    )
    assert _project(records)[-1][2]["status"] == "failed"


def _wire(tmp_path, session="session_abc"):
    path = tmp_path / "home" / "sessions" / "wd_ws_1" / session / "agents" / "main" / "wire.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"type": "metadata"}) + "\n")
    return path


@pytest.mark.no_db
def test_wire_tail_starts_at_end_and_reads_only_complete_lines(tmp_path):
    path = _wire(tmp_path)
    assert locate_wire([tmp_path / "missing", tmp_path / "home"], "session_abc") == path
    assert locate_wire([tmp_path / "home"], "../escape") is None
    tail = WireTail(path)
    tail.baseline()  # history is never replayed
    assert tail.read() == []
    with path.open("a") as handle:
        handle.write(json.dumps({"n": 1}) + "\nnot json\n" + '{"n": 2')
    assert tail.read() == [{"n": 1}]
    with path.open("a") as handle:
        handle.write("}\n")
    assert tail.read() == [{"n": 2}]
    path.write_text(json.dumps({"n": 3}) + "\n")  # truncated/rewritten journal
    assert tail.read() == []
    with path.open("a") as handle:
        handle.write(json.dumps({"n": 4}) + "\n")
    assert tail.read() == [{"n": 4}]


@pytest.mark.no_db
def test_journal_this_runtime_created_is_read_from_start(tmp_path):
    path = _wire(tmp_path)
    with path.open("a") as handle:
        handle.write(json.dumps({"n": 1}) + "\n")
    # Never baselined (created by this runtime's own process): all ours.
    assert WireTail(path).read() == [{"type": "metadata"}, {"n": 1}]


@pytest.mark.no_db
def test_capture_wire_baseline_pins_end_or_defers(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert capture_wire_baseline(str(tmp_path), None) is None
    assert capture_wire_baseline(str(tmp_path), "session_abc") is None  # no journal yet
    path = _wire(tmp_path)
    baseline = capture_wire_baseline(str(tmp_path), "session_abc")
    info = os.stat(path)
    assert baseline == WireBaseline("session_abc", path, (info.st_dev, info.st_ino), info.st_size)
    with path.open("a") as handle:
        handle.write(json.dumps({"n": 1}) + "\n")
    assert WireTail.from_baseline(baseline).read() == [{"n": 1}]
    # Journal present but unreadable (symlink): identity None → defer.
    target = tmp_path / "elsewhere.jsonl"
    target.write_text("")
    path.unlink()
    os.symlink(target, path)
    deferred = capture_wire_baseline(str(tmp_path), "session_abc")
    assert deferred is not None and deferred.identity is None


@pytest.mark.no_db
def test_wire_tail_refuses_symlinked_journal(tmp_path):
    path = _wire(tmp_path)
    target = tmp_path / "elsewhere.jsonl"
    target.write_text("")
    path.unlink()
    os.symlink(target, path)
    with pytest.raises(OSError):
        WireTail(path).read()


def _script(home, **unprompted):
    return {
        "capabilities": {"loadSession": False, "mcpCapabilities": {"http": False, "sse": False}},
        "agent_name": "Kimi Code CLI",
        "session_id": "session_938",
        "kimi_wire": {"home": str(home)},
        "on_prompt": [
            {
                "notify": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "已派发后台子代理"},
                }
            }
        ],
        "unprompted": {"delay": 1.5, **unprompted},
    }


def _agent_texts(service, session_id):
    return [
        message["content"].get("text")
        for message in service.list_messages(session_id, None)
        if message["kind"] == "text" and message["role"] == "agent"
    ]


def _published_unprompted_end(bus) -> bool:
    return any(
        payload.get("type") == "message"
        and payload["message"]["content"].get("event") == "turn_end"
        and payload["message"]["content"].get("unprompted") is True
        for _channel, payload in list(bus.events)
    )


@pytest.fixture
def kimi_home(tmp_path, monkeypatch):
    home = tmp_path / "kimi-code-home"
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    return home


def test_unprompted_wire_turn_is_persisted_and_published(chat, kimi_home):
    service, bus, register, workspace_id, user_id = chat
    register(_script(kimi_home, wire=TASK_TURN))
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.send_message(session["id"], workspace_id, "派一个后台子代理")
    # The human turn settles on the ACP thread; wait for it rather than rely
    # on the fake agent's unprompted delay outlasting it under load.
    wait_for_predicate(
        lambda: service.get_session(session["id"], workspace_id)["status"] == "idle", timeout=15
    )
    # The watcher appends (DB insert, then publish) the turn's rows one by
    # one; seeing the text row says nothing about the rows after it. Wait for
    # the last one — the unprompted turn_end, published after its insert — so
    # every earlier row is durable and published before asserting.
    wait_for_predicate(lambda: _published_unprompted_end(bus), timeout=15)
    assert "REPORT-938: 子代理已完成，结果 42" in _agent_texts(service, session["id"])
    messages = service.list_messages(session["id"], None)
    events = [m["content"].get("event") for m in messages if m["kind"] == "status"]
    assert "unprompted_turn" in events
    assert events[-1] == "turn_end"
    published = [
        payload["message"]["content"].get("text")
        for _channel, payload in bus.events
        if payload.get("type") == "message"
    ]
    assert "REPORT-938: 子代理已完成，结果 42" in published
    # Turn ownership is untouched: the session is idle and accepts a message.
    assert service.get_session(session["id"], workspace_id)["status"] == "idle"
    service.send_message(session["id"], workspace_id, "继续")


def test_out_of_turn_session_update_is_persisted(chat, kimi_home):
    """An agent that DOES push session/update with no prompt in flight already
    reaches the timeline (on_update has no turn gate; a trailing chunk folds
    into the turn's still-open row, #98) — the #938 gap is Kimi Code never
    sending those updates, not Studio discarding them."""
    service, _bus, register, workspace_id, user_id = chat
    notify = [
        {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "PUSHED"}}
    ]
    register(_script(kimi_home, notify=notify))
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.send_message(session["id"], workspace_id, "hi")
    wait_for_predicate(
        lambda: any("PUSHED" in (text or "") for text in _agent_texts(service, session["id"])),
        timeout=15,
    )


def test_closed_session_never_receives_unprompted_rows(chat, kimi_home):
    service, _bus, register, workspace_id, user_id = chat
    script = _script(kimi_home)
    del script["unprompted"]
    register(script)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    wire = locate_wire([kimi_home], "session_938")
    assert wire is not None
    service.close_session(session["id"], workspace_id)
    with wire.open("a") as handle:
        handle.writelines(json.dumps(record) + "\n" for record in TASK_TURN)
    # Two poll intervals past the journal write: nothing crosses the fence.
    # 保留的墙钟等待：跨过两个 journal 轮询周期，负向断言「无」行越过关闭围栏。
    time.sleep(2.5)
    assert _agent_texts(service, session["id"]) == []


LOAD_SCRIPT_938 = {
    "capabilities": {"loadSession": True, "mcpCapabilities": {"http": False, "sse": False}},
    "agent_name": "Kimi Code CLI",
    "session_id": "session_938",
    "on_prompt": [
        {
            "notify": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "ok"},
            }
        }
    ],
}
CRON_TURN = [
    {**TASK_TURN[0], "origin": {"kind": "cron_job", "jobId": "c1"}, "turnId": 4},
    {
        "type": "context.append_loop_event",
        "agentId": "main",
        "event": {
            "type": "content.part",
            "turnId": "4",
            "part": {"type": "text", "text": "CRON-REPORT-938: 定时检查完成"},
        },
    },
    {"type": "turn.ended", "agentId": "main", "turnId": 4, "reason": "completed"},
]


def _receipts(service, session_id):
    return [
        m
        for m in service.list_messages(session_id, None)
        if m["kind"] == "status" and m["content"].get("event") == "unprompted_turn"
    ]


def test_turn_written_during_session_load_is_persisted(chat, kimi_home):
    """#938 review R2 P1: on resume the engine may run a turn (cron fire)
    while session/load is still in flight — before on_ready. The baseline is
    taken before the new process is spawned, so that turn is projected, while
    journal history from before the resume is not replayed."""
    service, bus, register, workspace_id, user_id = chat
    register({**LOAD_SCRIPT_938, "kimi_wire": {"home": str(kimi_home)}})
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.send_message(session["id"], workspace_id, "hi")
    wait_for_predicate(
        lambda: service.get_session(session["id"], workspace_id)["status"] == "idle", timeout=15
    )
    service.close_session(session["id"], workspace_id)
    wire = locate_wire([kimi_home], "session_938")
    assert wire is not None
    with wire.open("a") as handle:  # history: written before the resume, never replayed
        handle.writelines(json.dumps(record) + "\n" for record in TASK_TURN)
    register(
        {
            **LOAD_SCRIPT_938,
            "kimi_wire": {"home": str(kimi_home)},
            "wire_before_load_reply": CRON_TURN,
        }
    )
    resumed = service.resume_session(session["id"], workspace_id, user_id)
    assert resumed["acp_session_id"] == "session_938"
    # Wait on the bus, not the DB: a row is inserted before it is published.
    wait_for_predicate(lambda: _published_unprompted_end(bus), timeout=15)
    texts = _agent_texts(service, session["id"])
    assert "CRON-REPORT-938: 定时检查完成" in texts
    assert "REPORT-938: 子代理已完成，结果 42" not in texts
    assert [m["content"]["origin"] for m in _receipts(service, session["id"])] == ["cron_job"]
    published = [
        payload["message"]["content"].get("text")
        for _channel, payload in list(bus.events)
        if payload.get("type") == "message"
    ]
    assert "CRON-REPORT-938: 定时检查完成" in published


class _HeldThread:
    """Captures the watcher loop instead of scheduling it (start-order barrier)."""

    targets: list = []

    def __init__(self, *, target, name, daemon):
        del name, daemon
        self.target = target

    def start(self):
        _HeldThread.targets.append(self.target)


@pytest.fixture
def held_watcher(admission, tmp_path, monkeypatch):
    """A runtime whose watcher thread is captured; ``poll()`` runs one step."""
    service, _db, sid, _workspace, runtime = admission
    bus = fixtures.RecordingBus()
    monkeypatch.setattr(service.store, "_bus", bus)
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path))
    runtime.kimi_agent = True
    _HeldThread.targets = []
    monkeypatch.setattr(unprompted_turns.threading, "Thread", _HeldThread)

    def poll():
        runtime.background_stop.set()  # one poll, then the loop exits
        (watch,) = _HeldThread.targets
        watch()
        return service.list_messages(sid, None)

    return service, sid, runtime, bus, poll


def _texts(messages):
    return [m["content"].get("text") for m in messages if m["kind"] == "text"]


def test_turn_written_before_first_poll_is_not_history(held_watcher, tmp_path):
    """#938 review R1: a loaded journal continues from the pre-spawn baseline,
    so a whole unprompted turn written after it — before on_ready or before
    the thread's first poll — is projected; pre-baseline history is not."""
    service, sid, runtime, bus, poll = held_watcher
    path = _wire(tmp_path, "session_race")
    with path.open("a") as handle:  # prior history: must not replay
        handle.write(json.dumps({**TASK_TURN[0], "turnId": 0}) + "\n")
    runtime.handle.loaded_existing = True
    runtime.wire_baseline = capture_wire_baseline(str(tmp_path), "session_race")
    with path.open("a") as handle:  # during session/load, before on_ready
        handle.writelines(json.dumps(record) + "\n" for record in TASK_TURN[:2])
    unprompted_turns.start_unprompted_watcher(service, sid, runtime, "session_race")
    with path.open("a") as handle:  # after on_ready, before the first poll
        handle.writelines(json.dumps(record) + "\n" for record in TASK_TURN[2:])
    messages = poll()
    assert "REPORT-938: 子代理已完成，结果 42" in _texts(messages)
    assert len([m for m in messages if m["content"].get("event") == "unprompted_turn"]) == 1
    published = [
        p["message"]["content"].get("text") for _c, p in bus.events if p.get("type") == "message"
    ]
    assert "REPORT-938: 子代理已完成，结果 42" in published


def test_own_journal_is_read_from_start_skipping_studio_turns(held_watcher, tmp_path):
    """A journal created by this runtime's process (session/new, including the
    resume fallback) is wholly ours: no baseline, read from the start; the
    Studio-driven turn in it is skipped by origin, the unprompted one lands."""
    service, sid, runtime, _bus, poll = held_watcher
    path = _wire(tmp_path, "session_new")
    with path.open("a") as handle:
        handle.write(json.dumps({**TASK_TURN[0], "origin": {"kind": "user"}, "turnId": 0}) + "\n")
        handle.writelines(json.dumps(record) + "\n" for record in TASK_TURN)
    runtime.handle.loaded_existing = False
    runtime.wire_baseline = None
    unprompted_turns.start_unprompted_watcher(service, sid, runtime, "session_new")
    messages = poll()
    assert "REPORT-938: 子代理已完成，结果 42" in _texts(messages)
    assert len([m for m in messages if m["content"].get("event") == "unprompted_turn"]) == 1


@pytest.mark.parametrize("missing", ["none", "other_session", "unreadable", "other_path"])
def test_loaded_journal_without_usable_baseline_never_replays(held_watcher, tmp_path, missing):
    """A loaded journal is never read from its start: without a usable
    pre-spawn baseline the watcher baselines at first sight (a possible miss,
    never a replay) and projects only what is written afterwards."""
    service, sid, runtime, _bus, poll = held_watcher
    path = _wire(tmp_path, "session_old")
    with path.open("a") as handle:  # history
        handle.writelines(json.dumps(record) + "\n" for record in TASK_TURN)
    good = capture_wire_baseline(str(tmp_path), "session_old")
    runtime.handle.loaded_existing = True
    runtime.wire_baseline = {
        "none": None,
        "other_session": WireBaseline("session_other", path, good.identity, 0),
        "unreadable": WireBaseline("session_old", path, None, 0),
        "other_path": WireBaseline("session_old", tmp_path / "x.jsonl", good.identity, 0),
    }[missing]
    unprompted_turns.start_unprompted_watcher(service, sid, runtime, "session_old")
    assert _texts(poll()) == []
    with path.open("a") as handle:
        handle.writelines(json.dumps(record) + "\n" for record in CRON_TURN)
    assert "CRON-REPORT-938: 定时检查完成" in _texts(poll())
