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

from server.app.studio_chat.kimi_wire import WireTail, locate_wire
from server.app.studio_chat.unprompted_turns import UnpromptedTurnProjector
from tests.helpers import studio_chat_fixtures as fixtures
from tests.helpers import wait_for_predicate

chat = fixtures.chat

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
    assert tail.read() == []  # baseline: history is never replayed
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
    wait_for_predicate(
        lambda: "REPORT-938: 子代理已完成，结果 42" in _agent_texts(service, session["id"]),
        timeout=15,
    )
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
    time.sleep(2.5)
    assert _agent_texts(service, session["id"]) == []
