"""Studio chat resume tests: closed/error sessions regain a live runtime.

Context rebuild comes in two flavours (service.resume_session): ACP
session/load when the freshly-spawned agent advertises loadSession, otherwise
a one-shot persisted-transcript injection into the first post-resume prompt.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from server.app.services.job_errors import ConflictError, InvalidOperationError, NotFoundError
from server.app.studio_chat import service as service_module
from server.app.studio_chat.prompts import STUDIO_AUTHORING_BOOTSTRAP
from server.app.studio_chat.registry import StudioAgentRegistryStore
from server.app.studio_chat.service import StudioChatService
from server.app.studio_chat.transcript import (
    RESUME_TRANSCRIPT_FOOTER,
    RESUME_TRANSCRIPT_HEADER,
    RESUME_TRANSCRIPT_MAX_CHARS,
    build_resume_transcript,
)
from tests.helpers import wait_for_predicate
from tests.postgres_support import TEST_DATABASE_URL

FAKE_AGENT = Path(__file__).resolve().parents[1] / "helpers" / "fake_acp_agent.py"

TEXT_SCRIPT = {
    "capabilities": {"loadSession": False, "mcpCapabilities": {"http": False, "sse": False}},
    "on_prompt": [
        {
            "notify": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "pong"},
            }
        }
    ],
}

LOAD_SCRIPT = {
    **TEXT_SCRIPT,
    "capabilities": {"loadSession": True, "mcpCapabilities": {"http": False, "sse": False}},
}

LOAD_FAILING_SCRIPT = {**LOAD_SCRIPT, "load_error": True}


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
    wait_for_predicate(condition, timeout=timeout, interval=interval)


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


def _read_sink(script_path: Path) -> list[dict]:
    sink = Path(str(script_path) + ".sink.jsonl")
    if not sink.exists():
        return []
    return [json.loads(line) for line in sink.read_text(encoding="utf-8").splitlines()]


def _requests(sink: list[dict], method: str) -> list[dict]:
    return [
        entry["received"] for entry in sink if entry.get("received", {}).get("method") == method
    ]


def _prompt_texts(sink: list[dict]) -> list[str]:
    texts = []
    for request in _requests(sink, "session/prompt"):
        texts.extend(block.get("text", "") for block in request["params"].get("prompt", []))
    return texts


def _turn_finished(service, session_id: str) -> bool:
    return service.get_session(session_id)["status"] == "idle"


def test_resume_closed_session_injects_transcript_once(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(TEXT_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.send_message(session["id"], workspace_id, "first question")
    _wait_for(lambda: _turn_finished(service, session["id"]))
    service.close_session(session["id"], workspace_id)
    with pytest.raises(ConflictError):
        service.send_message(session["id"], workspace_id, "while closed")

    resumed = service.resume_session(session["id"], workspace_id, user_id)
    assert resumed["status"] == "idle"
    assert resumed["closed_at"] is None
    # 历史消息原样保留。
    texts = [
        m
        for m in service.list_messages(session["id"], workspace_id)
        if m["kind"] == "text" and m["role"] == "user"
    ]
    assert [m["content"]["text"] for m in texts] == ["first question"]

    service.send_message(session["id"], workspace_id, "second question")
    _wait_for(lambda: _turn_finished(service, session["id"]))
    prompts = _prompt_texts(_read_sink(script_path))
    assert len(prompts) == 2
    # 恢复后首条 prompt：注入转录（含此前用户/助手文本），不重复 bootstrap。
    assert prompts[0].startswith(STUDIO_AUTHORING_BOOTSTRAP)
    assert RESUME_TRANSCRIPT_HEADER in prompts[1]
    assert "用户：first question" in prompts[1]
    assert "助手：pong" in prompts[1]
    assert not prompts[1].startswith(STUDIO_AUTHORING_BOOTSTRAP)
    assert prompts[1].endswith("second question")

    # 只注入一次：下一条 prompt 不再携带转录。
    service.send_message(session["id"], workspace_id, "third question")
    _wait_for(lambda: _turn_finished(service, session["id"]))
    prompts = _prompt_texts(_read_sink(script_path))
    assert len(prompts) == 3
    assert RESUME_TRANSCRIPT_HEADER not in prompts[2]
    # loadSession 未声明：全程没有 session/load。
    assert _requests(_read_sink(script_path), "session/load") == []


def test_resume_uses_session_load_when_advertised(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(LOAD_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.send_message(session["id"], workspace_id, "before close")
    _wait_for(lambda: _turn_finished(service, session["id"]))
    service.close_session(session["id"], workspace_id)

    resumed = service.resume_session(session["id"], workspace_id, user_id)
    assert resumed["status"] == "idle"

    sink = _read_sink(script_path)
    loads = _requests(sink, "session/load")
    assert len(loads) == 1
    assert loads[0]["params"]["sessionId"] == "fake-session-1"
    # resume 走 load：session/new 只有初次创建那一次，acp session id 不变。
    assert len(_requests(sink, "session/new")) == 1
    assert service.get_session(session["id"])["acp_session_id"] == "fake-session-1"

    # load 成功恢复了上下文：首条 prompt 不注入转录。
    service.send_message(session["id"], workspace_id, "after load")
    _wait_for(lambda: _turn_finished(service, session["id"]))
    prompts = _prompt_texts(_read_sink(script_path))
    assert RESUME_TRANSCRIPT_HEADER not in prompts[-1]


def test_resume_falls_back_to_transcript_when_load_fails(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(LOAD_FAILING_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.send_message(session["id"], workspace_id, "before close")
    _wait_for(lambda: _turn_finished(service, session["id"]))
    service.close_session(session["id"], workspace_id)

    resumed = service.resume_session(session["id"], workspace_id, user_id)
    assert resumed["status"] == "idle"

    sink = _read_sink(script_path)
    # load 失败后退回 session/new，并按转录路径重建上下文。
    assert len(_requests(sink, "session/load")) == 1
    assert len(_requests(sink, "session/new")) == 2
    service.send_message(session["id"], workspace_id, "after fallback")
    _wait_for(lambda: _turn_finished(service, session["id"]))
    prompts = _prompt_texts(_read_sink(script_path))
    assert RESUME_TRANSCRIPT_HEADER in prompts[-1]


def test_resume_live_session_is_idempotent_noop(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(TEXT_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")

    resumed = service.resume_session(session["id"], workspace_id, user_id)

    assert resumed["status"] == "idle"
    # 没有重新 spawn：initialize 只有创建时那一次。
    assert len(_requests(_read_sink(script_path), "initialize")) == 1


def test_resume_reaped_error_session_tears_down_stale_runtime(chat, job_db) -> None:
    """A zombie row reaped to error can be resumed even while the old
    (still-registered) runtime lingers: resume settles it before respawning,
    so no agent subprocess is orphaned."""
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(TEXT_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    reaped = job_db.reap_zombie_studio_chat_sessions()
    assert reaped == 1
    assert service.get_session(session["id"])["status"] == "error"
    assert service._runtime(session["id"]) is not None  # 旧 runtime 仍在册

    resumed = service.resume_session(session["id"], workspace_id, user_id)

    assert resumed["status"] == "idle"
    # 旧 runtime 已销毁、新 runtime 已 spawn（第二次 initialize）。
    assert len(_requests(_read_sink(script_path), "initialize")) == 2
    service.send_message(session["id"], workspace_id, "back from reap")
    _wait_for(lambda: _turn_finished(service, session["id"]))


def test_resume_respects_active_session_cap(chat, monkeypatch) -> None:
    service, _bus, register, workspace_id, user_id = chat
    register(TEXT_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.close_session(session["id"], workspace_id)

    monkeypatch.setattr(service_module, "MAX_ACTIVE_STUDIO_CHAT_SESSIONS", 0)
    with pytest.raises(ConflictError, match="Too many active"):
        service.resume_session(session["id"], workspace_id, user_id)
    # cap 拒绝不改状态：仍是 closed。
    assert service.get_session(session["id"])["status"] == "closed"


def test_resume_unknown_or_cross_workspace_session_is_not_found(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    register(TEXT_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.close_session(session["id"], workspace_id)

    with pytest.raises(NotFoundError):
        service.resume_session(session["id"], "other-ws", user_id)
    with pytest.raises(NotFoundError):
        service.resume_session("no-such-session", workspace_id, user_id)


def test_resume_with_unavailable_agent_fails_before_spawn(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    register(TEXT_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.close_session(session["id"], workspace_id)

    StudioAgentRegistryStore(TEST_DATABASE_URL).put(
        {
            "api_base": "http://127.0.0.1:8000",
            "agents": [
                {
                    "id": "fake-agent",
                    "label": "Fake Agent",
                    "command": "/nonexistent/fake-agent-binary",
                    "args": [],
                }
            ],
        }
    )
    with pytest.raises(InvalidOperationError, match="not available"):
        service.resume_session(session["id"], workspace_id, user_id)
    assert service.get_session(session["id"])["status"] == "closed"


def test_build_resume_transcript_filters_and_truncates() -> None:
    messages = [
        {"kind": "text", "role": "user", "content": {"text": "u1"}},
        {"kind": "tool_call", "role": "agent", "content": {"toolCallId": "tc"}},
        {"kind": "text", "role": "agent", "content": {"text": "a1"}},
        {"kind": "status", "role": "system", "content": {"event": "turn_end"}},
        {"kind": "text", "role": "user", "content": {"text": ""}},
    ]
    transcript = build_resume_transcript(messages)
    assert transcript.startswith(RESUME_TRANSCRIPT_HEADER)
    assert transcript.endswith(RESUME_TRANSCRIPT_FOOTER)
    assert "用户：u1" in transcript
    assert "助手：a1" in transcript
    assert "toolCallId" not in transcript
    assert "turn_end" not in transcript

    # 超长历史截断到字符预算内，保留最近对话。
    long_history = [
        {"kind": "text", "role": "user", "content": {"text": f"q{i} " + "x" * 1000}}
        for i in range(20)
    ]
    truncated = build_resume_transcript(long_history)
    assert len(truncated) <= RESUME_TRANSCRIPT_MAX_CHARS + len(RESUME_TRANSCRIPT_HEADER) + len(
        RESUME_TRANSCRIPT_FOOTER
    )
    assert "q19" in truncated
    assert "q0" not in truncated

    assert build_resume_transcript([]) == ""
    assert build_resume_transcript([messages[1]]) == ""
