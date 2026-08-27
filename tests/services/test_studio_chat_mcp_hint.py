"""MCP-visibility advisory hint tests (mcp_hint.py behaviour).

The smoke signal answers one question: has this session ever shown an
agent-legion MCP tool call? The hint is advisory, fires at most once per
session (persisted as mcp_status='unverified', so the guarantee survives
runtime rebuilds), and never on cancelled turns. Session-lifecycle happy
paths live in tests/services/test_studio_chat_service.py.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from server.app.studio_chat.registry import StudioAgentRegistryStore
from server.app.studio_chat.service import StudioChatService
from tests.postgres_support import TEST_DATABASE_URL

FAKE_AGENT = Path(__file__).resolve().parents[1] / "helpers" / "fake_acp_agent.py"

WAIT_CANCEL_SCRIPT = {"wait_for_cancel": True, "on_prompt": []}


class RecordingBus:
    """EventBus stand-in capturing published (channel, payload) pairs."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def attach_loop(self, loop) -> None:
        del loop

    def publish(self, channel: str, payload: str) -> None:
        self.events.append((channel, json.loads(payload)))

    def subscribe(self, channel: str):
        raise NotImplementedError

    def unsubscribe(self, channel: str, queue) -> None:
        del channel, queue


def _wait_for(condition, timeout: float = 20.0, interval: float = 0.05) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(interval)
    raise AssertionError("condition not met within timeout")


@pytest.fixture
def chat(job_db, settings, tmp_path):
    bus = RecordingBus()
    service = StudioChatService(job_db, settings, bus)
    store = StudioAgentRegistryStore(TEST_DATABASE_URL)

    def register(script: dict, agent_id: str = "fake-agent") -> Path:
        script_path = tmp_path / f"{agent_id}-script.json"
        script_path.write_text(json.dumps(script), encoding="utf-8")
        store.put(
            {
                "api_base": "http://127.0.0.1:8000",
                "agents": [
                    {
                        "id": agent_id,
                        "label": "Fake Agent",
                        "command": sys.executable,
                        "args": [str(FAKE_AGENT), str(script_path)],
                    }
                ],
            }
        )
        return script_path

    workspace_id = job_db.create_workspace(default_workflow_key="demo_workflow", name="Chat WS")[
        "id"
    ]
    user_id = str(job_db.create_user("chat-user", password_hash=None)["id"])
    yield service, bus, register, workspace_id, user_id
    service.shutdown()


def _status_events(service, session_id: str, workspace_id: str) -> list[str]:
    return [
        m["content"].get("event")
        for m in service.list_messages(session_id, workspace_id)
        if m["kind"] == "status"
    ]


def test_run_without_mcp_tool_call_is_flagged_unverified(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    register({"on_prompt": []})
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.send_message(session["id"], workspace_id, "hello")

    _wait_for(lambda: service.get_session(session["id"])["status"] == "idle")
    session = service.get_session(session["id"])
    assert session["mcp_status"] == "unverified"
    assert "mcp_unverified" in _status_events(service, session["id"], workspace_id)


def test_mcp_unverified_hint_is_shown_once_per_session(chat) -> None:
    """提示是会话级一次性的：第二个无工具轮次不再重复刷屏。"""
    service, _bus, register, workspace_id, user_id = chat
    register({"on_prompt": []})
    session = service.create_session(workspace_id, user_id, "fake-agent")
    for _ in range(2):
        service.send_message(session["id"], workspace_id, "hello")
        _wait_for(lambda: service.get_session(session["id"])["status"] == "idle")

    assert _status_events(service, session["id"], workspace_id).count("mcp_unverified") == 1


def test_mcp_unverified_hint_is_not_repeated_after_runtime_rebuild(chat) -> None:
    """已提示过（mcp_status='unverified' 落库）的会话，runtime 重建
    （后端重启 / ACP 进程重连）后不得再提示：一次性由 DB 兜底。"""
    service, _bus, register, workspace_id, user_id = chat
    register({"on_prompt": []})
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.send_message(session["id"], workspace_id, "hello")
    _wait_for(lambda: service.get_session(session["id"])["status"] == "idle")

    # 模拟 runtime 重建：内存标志丢失，只剩 DB 里的 mcp_status。
    runtime = service.runtime(session["id"])
    assert runtime is not None
    with runtime.lock:
        runtime.mcp_hint_shown = False

    service.send_message(session["id"], workspace_id, "hello again")
    _wait_for(lambda: service.get_session(session["id"])["status"] == "idle")
    assert _status_events(service, session["id"], workspace_id).count("mcp_unverified") == 1


def test_cancelled_turn_does_not_raise_mcp_unverified(chat) -> None:
    """用户取消的轮次不是接线问题的证据：不产生 mcp 信号。"""
    service, _bus, register, workspace_id, user_id = chat
    register(WAIT_CANCEL_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.send_message(session["id"], workspace_id, "accidental submit")
    _wait_for(lambda: service.get_session(session["id"])["status"] == "running")

    service.cancel(session["id"], workspace_id)
    _wait_for(lambda: service.get_session(session["id"])["status"] == "idle")
    session = service.get_session(session["id"])
    assert session["mcp_status"] == "unknown"
    assert "mcp_unverified" not in _status_events(service, session["id"], workspace_id)
