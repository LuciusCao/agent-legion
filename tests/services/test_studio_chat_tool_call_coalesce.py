"""tool_call / tool_call_update frames coalesce in place into one row (#1120).

Streamed text chunks already fold into a single row per turn
(``store.append_stream_chunk``); tool frames used to append N full-snapshot
rows per call, multiplying DB rows and frontend copies and flooding the
500-row history window. The store now keeps one row per ``toolCallId``: the
first frame inserts it, later frames shallow-merge into it and publish a
no-seq half-row SSE frame (the stream text update shape the frontend's
``upsertMessage`` already folds in by id).
"""

from __future__ import annotations

import json
from typing import Any

from server.app.studio_chat.unprompted_turns import UnpromptedTurnWatcher
from tests.helpers import studio_chat_fixtures as fixtures
from tests.helpers import wait_for_predicate

chat = fixtures.chat
admission = fixtures.admission


class _FlagBus(fixtures.RecordingBus):
    """RecordingBus that also captures each publish's replaceable flag."""

    def __init__(self) -> None:
        super().__init__()
        self.replaceable: list[bool] = []

    def publish(self, channel: str, payload: str, *, replaceable: bool = False) -> None:
        self.replaceable.append(replaceable)
        super().publish(channel, payload, replaceable=replaceable)


def _tool_call(tc: str = "tc-1", **extra: Any) -> dict[str, Any]:
    return {
        "sessionUpdate": "tool_call",
        "toolCallId": tc,
        "title": "Bash",
        "kind": "execute",
        "status": "in_progress",
        "rawInput": {"command": "ls"},
        **extra,
    }


def _tool_update(tc: str = "tc-1", **extra: Any) -> dict[str, Any]:
    return {"sessionUpdate": "tool_call_update", "toolCallId": tc, **extra}


def _tool_rows(service: Any, session_id: str) -> list[dict[str, Any]]:
    return [m for m in service.list_messages(session_id, None) if m["kind"] == "tool_call"]


def _frames_for(bus: _FlagBus, message_id: str) -> list[dict[str, Any]]:
    return [
        payload["message"]
        for _channel, payload in bus.events
        if payload.get("type") == "message" and payload["message"].get("id") == message_id
    ]


def test_updates_merge_in_place_into_one_row(admission, monkeypatch) -> None:
    service, _db, sid, _workspace, runtime = admission
    bus = _FlagBus()
    monkeypatch.setattr(service.store, "_bus", bus)
    first = service.store.append_tool_call(sid, runtime, _tool_call())
    service.store.append_tool_call(sid, runtime, _tool_update(status="in_progress"))
    service.store.append_tool_call(
        sid, runtime, _tool_update(status="completed", rawOutput={"out": "done"})
    )
    rows = _tool_rows(service, sid)
    assert [row["id"] for row in rows] == [first["id"]]
    content = rows[0]["content"]
    # 浅合并：首帧的 title/kind/rawInput 保留，最终帧的 status/rawOutput 完整。
    assert content["title"] == "Bash" and content["kind"] == "execute"
    assert content["rawInput"] == {"command": "ls"}
    assert content["status"] == "completed"
    assert content["rawOutput"] == {"out": "done"}
    # 终态剪枝：归属 map 只挂在途调用，completed 后条目即移除。
    assert runtime.tool_call_messages == {}
    # SSE：首帧是带 seq 的全行；两次更新是同 id 的无 seq 半行、replaceable。
    frames = _frames_for(bus, first["id"])
    assert len(frames) == 3
    assert isinstance(frames[0].get("seq"), int) and "created_at" in frames[0]
    assert bus.replaceable == [False, True, True]
    for frame in frames[1:]:
        assert "seq" not in frame and "created_at" not in frame
        assert frame["kind"] == "tool_call" and frame["role"] == "agent"
        assert frame["content"]["toolCallId"] == "tc-1"
    assert frames[-1]["content"]["rawOutput"] == {"out": "done"}
    assert frames[-1]["content"]["title"] == "Bash"  # 半行帧携带合并后的完整快照


def test_shallow_merge_replaces_nested_values(admission) -> None:
    """``{**old, **new}``：嵌套键整体替换，不深合并、不拼接。"""
    service, _db, sid, _workspace, runtime = admission
    service.store.append_tool_call(sid, runtime, _tool_call(content=[{"text": "old"}]))
    service.store.append_tool_call(
        sid, runtime, _tool_update(status="in_progress", content=[{"text": "new"}])
    )
    (row,) = _tool_rows(service, sid)
    assert row["content"]["content"] == [{"text": "new"}]


def test_distinct_tool_call_ids_keep_separate_rows(admission) -> None:
    service, _db, sid, _workspace, runtime = admission
    service.store.append_tool_call(sid, runtime, _tool_call("tc-1"))
    service.store.append_tool_call(sid, runtime, _tool_call("tc-2", title="Read"))
    service.store.append_tool_call(sid, runtime, _tool_update("tc-1", status="completed"))
    rows = _tool_rows(service, sid)
    assert len(rows) == 2
    by_id = {row["content"]["toolCallId"]: row for row in rows}
    assert by_id["tc-1"]["content"]["status"] == "completed"
    assert by_id["tc-2"]["content"]["status"] == "in_progress"
    assert by_id["tc-2"]["content"]["title"] == "Read"


def test_terminal_first_frame_is_not_tracked(admission) -> None:
    """单帧即终态的调用（如已完成的 tool_call）不占用归属 map。"""
    service, _db, sid, _workspace, runtime = admission
    service.store.append_tool_call(sid, runtime, _tool_call(status="completed"))
    assert runtime.tool_call_messages == {}
    assert len(_tool_rows(service, sid)) == 1


def test_missing_runtime_falls_back_to_per_frame_rows(admission) -> None:
    """runtime 缺失（teardown 竞态的迟到更新）：保持旧行为，逐帧落新行。"""
    service, _db, sid, _workspace, _runtime = admission
    service.store.append_tool_call(sid, None, _tool_call())
    service.store.append_tool_call(sid, None, _tool_update(status="completed"))
    assert len(_tool_rows(service, sid)) == 2


def test_resume_with_empty_map_lands_update_as_new_row(admission) -> None:
    """归属 map 只在内存：resume/重启后旧 toolCallId 的更新退化为新行（行为
    退化不丢数据）；新行重新登记后，后续更新照常合并进它。"""
    service, _db, sid, _workspace, runtime = admission
    service.store.append_tool_call(sid, runtime, _tool_call())
    runtime.tool_call_messages.clear()  # 模拟 resume 后全新 runtime 的空 map
    service.store.append_tool_call(sid, runtime, _tool_update(status="in_progress"))
    service.store.append_tool_call(
        sid, runtime, _tool_update(status="completed", rawOutput={"out": "done"})
    )
    rows = _tool_rows(service, sid)
    assert len(rows) == 2
    assert rows[1]["content"]["sessionUpdate"] == "tool_call_update"
    assert rows[1]["content"]["status"] == "completed"
    assert rows[1]["content"]["rawOutput"] == {"out": "done"}


def test_missing_tool_call_id_appends_plain_row(admission) -> None:
    """帧不带 toolCallId：无法归属，保持逐帧落行。"""
    service, _db, sid, _workspace, runtime = admission
    frame = {"sessionUpdate": "tool_call", "title": "mystery"}
    service.store.append_tool_call(sid, runtime, frame)
    service.store.append_tool_call(sid, runtime, frame)
    assert len(_tool_rows(service, sid)) == 2


_UNPROMPTED_WIRE = [
    {
        "type": "turn.prompt",
        "agentId": "main",
        "origin": {"kind": "task", "taskId": "agent-1", "status": "completed"},
        "turnId": 7,
    },
    {
        "type": "context.append_loop_event",
        "agentId": "main",
        "event": {
            "type": "tool.call",
            "turnId": "7",
            "toolCallId": "call_1",
            "name": "Bash",
            "description": "list files",
            "args": {"command": "ls"},
        },
    },
    {
        "type": "context.append_loop_event",
        "agentId": "main",
        "event": {"type": "tool.result", "toolCallId": "call_1", "result": {"output": "file-a"}},
    },
]


def test_unprompted_projection_coalesces_tool_rows(admission, tmp_path) -> None:
    """#938 投影路径同样合并：wire journal 的 tool.call + tool.result 落一行。"""
    service, _db, sid, _workspace, runtime = admission
    path = tmp_path / "wire.jsonl"
    path.write_text("\n".join(json.dumps(record) for record in _UNPROMPTED_WIRE) + "\n")
    watcher = UnpromptedTurnWatcher(service, sid, runtime, lambda: path)
    watcher.step()
    (row,) = _tool_rows(service, sid)
    content = row["content"]
    assert content["toolCallId"] == "7:call_1"
    assert content["title"] == "list files" and content["kind"] == "execute"
    assert content["status"] == "completed" and content["rawOutput"] == "file-a"


_TOOL_STREAM_SCRIPT = {
    "capabilities": {"loadSession": False, "mcpCapabilities": {"http": False, "sse": False}},
    "on_prompt": [
        {"notify": _tool_call()},
        {"notify": _tool_update(status="in_progress", rawOutput={"chunk": "a"})},
        {"notify": _tool_update(status="completed", rawOutput={"out": "done"})},
    ],
}


def test_acp_tool_updates_persist_as_one_row(chat) -> None:
    """端到端（fake ACP agent）：一次工具调用三次更新 → DB 一行 + 三帧 SSE。"""
    service, bus, register, workspace_id, user_id = chat
    register(_TOOL_STREAM_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.send_message(session["id"], workspace_id, "run ls")
    wait_for_predicate(
        lambda: service.get_session(session["id"], workspace_id)["status"] == "idle", timeout=15
    )
    rows = _tool_rows(service, session["id"])
    assert len(rows) == 1
    content = rows[0]["content"]
    assert content["title"] == "Bash" and content["rawInput"] == {"command": "ls"}
    assert content["status"] == "completed" and content["rawOutput"] == {"out": "done"}
    frames = _frames_for(bus, rows[0]["id"])
    assert len(frames) == 3
    assert isinstance(frames[0].get("seq"), int)
    assert all("seq" not in frame for frame in frames[1:])
