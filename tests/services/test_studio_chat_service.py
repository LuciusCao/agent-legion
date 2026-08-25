"""Studio chat service lifecycle tests against the scriptable fake ACP agent."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path

import pytest
from acp.schema import HttpMcpServer

from server.app.auth.scoped_tokens import authenticate_scoped_token
from server.app.services.job_errors import ConflictError, InvalidOperationError, NotFoundError
from server.app.studio_chat import permissions as permissions_module
from server.app.studio_chat import service as service_module
from server.app.studio_chat.acp_session import AcpSessionHandle
from server.app.studio_chat.prompts import STUDIO_AUTHORING_BOOTSTRAP
from server.app.studio_chat.registry import StudioAgentRegistryStore
from server.app.studio_chat.runtime import SessionRuntime
from server.app.studio_chat.service import StudioChatService
from tests.postgres_support import TEST_DATABASE_URL

FAKE_AGENT = Path(__file__).resolve().parents[1] / "helpers" / "fake_acp_agent.py"

TEXT_SCRIPT = {
    "capabilities": {"loadSession": False, "mcpCapabilities": {"http": False, "sse": False}},
    "on_prompt": [
        {
            "notify": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "Hello"},
            }
        },
        {
            "notify": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": " world"},
            }
        },
        {
            "notify": {
                "sessionUpdate": "tool_call",
                "toolCallId": "tc-1",
                "title": "agent-legion-studio__list_workflows",
                "kind": "other",
                "status": "completed",
            }
        },
    ],
}

INTERRUPTED_TEXT_SCRIPT = {
    "capabilities": {"loadSession": False, "mcpCapabilities": {"http": False, "sse": False}},
    "on_prompt": [
        {
            "notify": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "step one,"},
            }
        },
        {
            "notify": {
                "sessionUpdate": "tool_call",
                "toolCallId": "tc-mid",
                "title": "agent-legion-studio__validate_workflow",
                "kind": "other",
                "status": "completed",
            }
        },
        {
            "notify": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "step two."},
            }
        },
    ],
}

MCP_PERMISSION_SCRIPT = {
    "on_prompt": [
        {
            "permission": {
                "toolCall": {
                    "toolCallId": "tc-mcp",
                    "title": "agent-legion-studio__validate_workflow",
                },
                "options": [
                    {"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                    {"optionId": "deny", "name": "Deny", "kind": "reject_once"},
                ],
            }
        }
    ],
}

HUMAN_PERMISSION_SCRIPT = {
    "on_prompt": [
        {
            "permission": {
                "toolCall": {"toolCallId": "tc-bash", "title": "Bash: ls"},
                "options": [
                    {"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                    {"optionId": "deny", "name": "Deny", "kind": "reject_once"},
                ],
            }
        }
    ],
}

# A local Bash call whose rawInput merely mentions platform tool names; the
# identity fields (title/kind) carry no MCP reference, so this must take the
# human-confirmation path instead of an MCP auto-approve.
LOCAL_BASH_MIMIC_SCRIPT = {
    "on_prompt": [
        {
            "permission": {
                "toolCall": {
                    "toolCallId": "tc-local-bash",
                    "title": "Bash",
                    "kind": "execute",
                    "rawInput": {
                        "command": (
                            "grep -rn agent-legion-studio . && validate_workflow draft.yaml"
                        )
                    },
                },
                "options": [
                    {"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                    {"optionId": "deny", "name": "Deny", "kind": "reject_once"},
                ],
            }
        }
    ],
}

# A local read-only tool call (ACP kind "read"/"search" — the Read/Glob/Grep
# class): auto-approved without a human roundtrip (side-effect-free).
READ_ONLY_PERMISSION_SCRIPT = {
    "on_prompt": [
        {
            "permission": {
                "toolCall": {"toolCallId": "tc-read", "title": "Read", "kind": "read"},
                "options": [
                    {"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                    {"optionId": "deny", "name": "Deny", "kind": "reject_once"},
                ],
            }
        }
    ],
}

WAIT_CANCEL_SCRIPT = {"wait_for_cancel": True, "on_prompt": []}

THOUGHT_SCRIPT = {
    "on_prompt": [
        {
            "notify": {
                "sessionUpdate": "agent_thought_chunk",
                "content": {"type": "text", "text": "先想"},
            }
        },
        {
            "notify": {
                "sessionUpdate": "agent_thought_chunk",
                "content": {"type": "text", "text": "一下"},
            }
        },
        {
            "notify": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "答案"},
            }
        },
    ],
}


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


def _read_sink(script_path: Path) -> list[dict]:
    sink = Path(str(script_path) + ".sink.jsonl")
    if not sink.exists():
        return []
    return [json.loads(line) for line in sink.read_text(encoding="utf-8").splitlines()]


def _prompt_texts(sink: list[dict]) -> list[str]:
    texts = []
    for entry in sink:
        message = entry.get("received", {})
        if message.get("method") == "session/prompt":
            texts.extend(block.get("text", "") for block in message["params"].get("prompt", []))
    return texts


def _new_session_mcp_headers(sink: list[dict]) -> dict[str, str]:
    for entry in sink:
        message = entry.get("received", {})
        if message.get("method") == "session/new":
            servers = message["params"].get("mcpServers", [])
            assert servers, "session/new carried no MCP servers"
            server = servers[0]
            # kimi >= 0.38 rejects ACP stdio MCP entries: the injection must
            # be the in-app HTTP endpoint with credentials in headers.
            assert server.get("type") == "http"
            assert str(server.get("url", "")).endswith("/api/studio-agent/mcp")
            return {item["name"]: item["value"] for item in server.get("headers", [])}
    raise AssertionError("session/new never reached the fake agent")


def _bearer_token(headers: dict[str, str]) -> str:
    return headers["Authorization"].removeprefix("Bearer ")


def test_run_token_is_bound_to_the_session_workspace(chat, job_db) -> None:
    """Schema v45: the per-session run token records the workspace binding so
    the tool surface can refuse other workspaces for it (STUDIO-AGENT-001)."""
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(TEXT_SCRIPT)
    service.create_session(workspace_id, user_id, "fake-agent")

    token = _bearer_token(_new_session_mcp_headers(_read_sink(script_path)))
    resolved = authenticate_scoped_token(job_db, token)
    assert resolved is not None
    assert resolved["scoped_workspace_id"] == workspace_id


def test_mcp_headers_carry_the_chat_session_id(chat) -> None:
    """The get_studio_context tool resolves its session through this header."""
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(TEXT_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")

    headers = _new_session_mcp_headers(_read_sink(script_path))
    assert headers["x-agent-legion-mcp-session-id"] == session["id"]


def test_set_selected_node_roundtrip(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    register(TEXT_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")

    updated = service.set_selected_node(session["id"], workspace_id, "node-a")
    assert updated["selected_node_key"] == "node-a"
    cleared = service.set_selected_node(session["id"], workspace_id, None)
    assert cleared["selected_node_key"] is None
    with pytest.raises(NotFoundError):
        service.set_selected_node(session["id"], "other-ws", "node-a")


def test_session_lifecycle_turn_and_token_revocation(chat, job_db) -> None:
    service, bus, register, workspace_id, user_id = chat
    script_path = register(TEXT_SCRIPT)

    session = service.create_session(workspace_id, user_id, "fake-agent")
    assert session["status"] == "idle"
    assert session["acp_session_id"] == "fake-session-1"
    assert session["capability_snapshot"]["loadSession"] is False

    service.send_message(session["id"], workspace_id, "list my workflows")
    _wait_for(lambda: service.get_session(session["id"])["status"] == "idle")
    _wait_for(
        lambda: any(
            m["kind"] == "text" and m["role"] == "agent"
            for m in service.list_messages(session["id"], workspace_id)
        )
    )

    messages = service.list_messages(session["id"], workspace_id)
    agent_texts = [m for m in messages if m["kind"] == "text" and m["role"] == "agent"]
    # Chunks are coalesced into one persisted agent message per turn.
    assert len(agent_texts) == 1
    assert agent_texts[0]["content"]["text"] == "Hello world"
    tool_calls = [m for m in messages if m["kind"] == "tool_call"]
    assert tool_calls and tool_calls[0]["content"]["toolCallId"] == "tc-1"

    session = service.get_session(session["id"])
    assert session["mcp_status"] == "verified"

    sink = _read_sink(script_path)
    # First prompt carries the built-in authoring bootstrap (decision 8).
    assert _prompt_texts(sink)[0].startswith(STUDIO_AUTHORING_BOOTSTRAP)
    token = _bearer_token(_new_session_mcp_headers(sink))
    # The minted scoped token is live for the session's lifetime...
    assert authenticate_scoped_token(job_db, token) is not None

    # SSE payload stream saw persisted messages and session snapshots.
    types = [payload["type"] for _, payload in bus.events]
    assert "message" in types and "session" in types
    assert all(token not in json.dumps(payload) for _, payload in bus.events)

    closed = service.close_session(session["id"], workspace_id)
    assert closed["status"] == "closed"
    # ...and closing the session revokes it (token never persists anywhere).
    assert authenticate_scoped_token(job_db, token) is None
    with pytest.raises(ConflictError):
        service.send_message(session["id"], workspace_id, "again")


def test_tool_call_splits_turn_text_into_interleaved_messages(chat) -> None:
    """A tool call mid-turn closes the open text row: chunks after it start a
    fresh message below the tool card, so rendering interleaves by seq instead
    of stacking all text above every tool call."""
    service, _bus, register, workspace_id, user_id = chat
    register(INTERRUPTED_TEXT_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")

    service.send_message(session["id"], workspace_id, "do two steps")
    _wait_for(
        lambda: (
            len(
                [
                    m
                    for m in service.list_messages(session["id"], workspace_id)
                    if m["kind"] == "text" and m["role"] == "agent"
                ]
            )
            == 2
        )
    )

    messages = service.list_messages(session["id"], workspace_id)
    visible = [
        m
        for m in messages
        if m["kind"] == "tool_call" or (m["kind"] == "text" and m["role"] == "agent")
    ]
    assert [m["kind"] for m in visible] == ["text", "tool_call", "text"]
    assert visible[0]["content"]["text"] == "step one,"
    assert visible[1]["content"]["toolCallId"] == "tc-mid"
    assert visible[2]["content"]["text"] == "step two."


def test_thought_chunks_persist_as_coalesced_thought_message(chat) -> None:
    """agent_thought_chunk 不再丢弃：按 turn 聚合落库为一条 thought 消息
    （前端可折叠），与正文 text 消息分开，且经 SSE 透传。"""
    service, bus, register, workspace_id, user_id = chat
    register(THOUGHT_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.send_message(session["id"], workspace_id, "think it through")

    _wait_for(lambda: service.get_session(session["id"])["status"] == "idle")
    messages = service.list_messages(session["id"], workspace_id)
    thoughts = [m for m in messages if m["kind"] == "thought"]
    assert len(thoughts) == 1
    assert thoughts[0]["role"] == "agent"
    assert thoughts[0]["content"]["text"] == "先想一下"
    agent_texts = [m for m in messages if m["kind"] == "text" and m["role"] == "agent"]
    assert len(agent_texts) == 1
    assert agent_texts[0]["content"]["text"] == "答案"
    thought_events = [
        payload
        for _, payload in bus.events
        if payload.get("type") == "message" and payload["message"].get("kind") == "thought"
    ]
    assert thought_events


def test_run_without_mcp_tool_call_is_flagged_unverified(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    register({"on_prompt": []})
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.send_message(session["id"], workspace_id, "hello")

    _wait_for(lambda: service.get_session(session["id"])["status"] == "idle")
    session = service.get_session(session["id"])
    assert session["mcp_status"] == "unverified"
    events = [
        m["content"].get("event")
        for m in service.list_messages(session["id"], workspace_id)
        if m["kind"] == "status"
    ]
    assert "mcp_unverified" in events


def test_agent_legion_tool_permission_auto_approves(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(MCP_PERMISSION_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.send_message(session["id"], workspace_id, "validate this")

    _wait_for(lambda: service.get_session(session["id"])["status"] == "idle")
    outcomes = [
        e["permission_outcome"] for e in _read_sink(script_path) if "permission_outcome" in e
    ]
    assert outcomes == [{"outcome": "selected", "optionId": "allow"}]
    assert service.get_session(session["id"])["mcp_status"] == "verified"
    # Auto-approvals never park the session in awaiting_permission.
    assert "awaiting_permission" not in [
        service.get_session(session["id"])["status"],
    ]


def test_human_permission_forward_answer_and_allow_all(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(HUMAN_PERMISSION_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.send_message(session["id"], workspace_id, "run ls")

    _wait_for(lambda: service.get_session(session["id"])["status"] == "awaiting_permission")
    pending = [
        m
        for m in service.list_messages(session["id"], workspace_id)
        if m["kind"] == "permission" and m["content"].get("status") == "pending"
    ]
    assert len(pending) == 1
    request_id = pending[0]["content"]["request_id"]
    service.respond_permission(session["id"], workspace_id, request_id, option_id="deny", deny=True)

    _wait_for(lambda: service.get_session(session["id"])["status"] == "idle")
    outcomes = [
        e["permission_outcome"] for e in _read_sink(script_path) if "permission_outcome" in e
    ]
    assert outcomes == [{"outcome": "cancelled"}]

    # The session-level allow-all switch approves the next non-MCP prompt
    # without a human roundtrip.
    service.set_allow_all_permissions(session["id"], workspace_id, True)
    service.send_message(session["id"], workspace_id, "run ls again")
    _wait_for(lambda: service.get_session(session["id"])["status"] == "idle")
    outcomes = [
        e["permission_outcome"] for e in _read_sink(script_path) if "permission_outcome" in e
    ]
    assert outcomes[-1] == {"outcome": "selected", "optionId": "allow"}


def test_read_only_tool_permission_auto_approves(chat) -> None:
    """Read 类只读本地工具（kind=read/search）自动批准，不经人工确认；
    写/Bash 类仍走人工（由 HUMAN_PERMISSION_SCRIPT 系列测试覆盖）。"""
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(READ_ONLY_PERMISSION_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.send_message(session["id"], workspace_id, "read the draft")

    _wait_for(lambda: service.get_session(session["id"])["status"] == "idle")
    outcomes = [
        e["permission_outcome"] for e in _read_sink(script_path) if "permission_outcome" in e
    ]
    assert outcomes == [{"outcome": "selected", "optionId": "allow"}]
    decisions = [
        m["content"]["decision"]
        for m in service.list_messages(session["id"], workspace_id)
        if m["kind"] == "permission" and m["content"].get("status") == "resolved"
    ]
    assert decisions and decisions[-1]["via"] == "auto_read_only"
    # 只读自动批准不park会话、也不计为 MCP 可见性信号。
    assert service.get_session(session["id"])["mcp_status"] == "unverified"


def test_permission_timeout_is_bounded() -> None:
    """Guard: the human permission wait must stay short enough that an
    abandoned tab cannot park a turn for long (#91 follow-up: 900s → 120s)."""
    assert permissions_module.PERMISSION_TIMEOUT_SECONDS == 120


def test_local_command_mentioning_tool_names_is_not_auto_approved(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    register(LOCAL_BASH_MIMIC_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.send_message(session["id"], workspace_id, "grep the repo")

    # rawInput mentions server/tool names, but identity fields do not: the
    # request parks for human confirmation instead of auto-approving.
    _wait_for(lambda: service.get_session(session["id"])["status"] == "awaiting_permission")
    pending = [
        m
        for m in service.list_messages(session["id"], workspace_id)
        if m["kind"] == "permission" and m["content"].get("status") == "pending"
    ]
    assert len(pending) == 1
    service.respond_permission(
        session["id"],
        workspace_id,
        pending[0]["content"]["request_id"],
        option_id="deny",
        deny=True,
    )
    _wait_for(lambda: service.get_session(session["id"])["status"] == "idle")


def test_close_during_pending_permission_stays_closed(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    register(HUMAN_PERMISSION_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.send_message(session["id"], workspace_id, "run ls")
    _wait_for(lambda: service.get_session(session["id"])["status"] == "awaiting_permission")
    runtime = service._runtime(session["id"])
    assert runtime is not None

    service.close_session(session["id"], workspace_id)

    # Teardown settles the pending permission as denied; the waiter thread's
    # finally must not resurrect the closed session back to running.
    _wait_for(lambda: not runtime.pending_permissions)
    time.sleep(0.2)  # let a regressed finally's status write land
    assert service.get_session(session["id"])["status"] == "closed"


def test_ready_callback_does_not_revive_closed_session(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    register(TEXT_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.close_session(session["id"], workspace_id)

    service._on_ready(session["id"], {}, "late-acp-session")

    assert service.get_session(session["id"])["status"] == "closed"


def test_cancel_settles_pending_permission(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(HUMAN_PERMISSION_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.send_message(session["id"], workspace_id, "run ls")

    _wait_for(lambda: service.get_session(session["id"])["status"] == "awaiting_permission")
    service.cancel(session["id"], workspace_id)

    _wait_for(lambda: service.get_session(session["id"])["status"] == "idle")
    outcomes = [
        e["permission_outcome"] for e in _read_sink(script_path) if "permission_outcome" in e
    ]
    assert outcomes == [{"outcome": "cancelled"}]


def test_busy_session_rejects_second_message_and_cancel_frees_it(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    register(WAIT_CANCEL_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.send_message(session["id"], workspace_id, "long turn")
    _wait_for(lambda: service.get_session(session["id"])["status"] == "running")

    with pytest.raises(ConflictError):
        service.send_message(session["id"], workspace_id, "second")

    service.cancel(session["id"], workspace_id)
    _wait_for(lambda: service.get_session(session["id"])["status"] == "idle")
    stop_events = [
        m["content"]
        for m in service.list_messages(session["id"], workspace_id)
        if m["kind"] == "status" and m["content"].get("event") == "turn_end"
    ]
    assert stop_events and stop_events[-1]["stop_reason"] == "cancelled"


def test_unknown_agent_id_is_rejected(chat) -> None:
    service, _bus, _register, workspace_id, user_id = chat
    with pytest.raises(InvalidOperationError):
        service.create_session(workspace_id, user_id, "no-such-agent")


def test_agent_startup_failure_marks_session_error(chat, tmp_path) -> None:
    service, _bus, _register, workspace_id, user_id = chat
    # The command resolves on PATH (passes the availability probe) but the
    # process exits immediately: python with a missing script path.
    StudioAgentRegistryStore(TEST_DATABASE_URL).put(
        {
            "api_base": "http://127.0.0.1:8000",
            "agents": [
                {
                    "id": "broken-agent",
                    "label": "Broken",
                    "command": sys.executable,
                    "args": ["/nonexistent/fake-agent-script.py"],
                }
            ],
        }
    )
    with pytest.raises(InvalidOperationError):
        service.create_session(workspace_id, user_id, "broken-agent")
    sessions = service.list_sessions(workspace_id)
    assert sessions and sessions[0]["status"] == "error"


def test_create_session_failure_cleans_up_row_token_and_runtime(chat, job_db, monkeypatch) -> None:
    """A failure between session-row creation and runtime registration must
    not leave a 'starting' row, a live scoped token, or a registered runtime."""
    service, _bus, register, workspace_id, user_id = chat
    register(TEXT_SCRIPT)
    minted: list[str] = []
    real_mint = service_module.mint_scoped_token

    def capturing_mint(*args, **kwargs):
        token = real_mint(*args, **kwargs)
        minted.append(token)
        return token

    def exploding_handle(**kwargs):
        raise RuntimeError("spawn blew up")

    monkeypatch.setattr(service_module, "mint_scoped_token", capturing_mint)
    monkeypatch.setattr(service_module, "AcpSessionHandle", exploding_handle)

    with pytest.raises(RuntimeError, match="spawn blew up"):
        service.create_session(workspace_id, user_id, "fake-agent")

    sessions = service.list_sessions(workspace_id)
    assert sessions and sessions[0]["status"] == "error"
    assert minted and authenticate_scoped_token(job_db, minted[0]) is None
    assert service._runtime(sessions[0]["id"]) is None


def test_create_session_mint_failure_still_clears_starting_row(chat, monkeypatch) -> None:
    """A failure inside token minting (before the handle exists) must also
    funnel through the cleanup path: no 'starting' residue, no revoke of a
    token that never materialized (#91 review follow-up)."""
    service, _bus, register, workspace_id, user_id = chat
    register(TEXT_SCRIPT)

    def exploding_mint(*args, **kwargs):
        raise RuntimeError("mint blew up")

    monkeypatch.setattr(service_module, "mint_scoped_token", exploding_mint)

    with pytest.raises(RuntimeError, match="mint blew up"):
        service.create_session(workspace_id, user_id, "fake-agent")

    sessions = service.list_sessions(workspace_id)
    assert sessions and sessions[0]["status"] == "error"
    assert service._runtime(sessions[0]["id"]) is None


def test_busy_claim_rejects_second_sender_without_duplicate_user_message(chat) -> None:
    """The idle -> running claim is atomic: a concurrent second sender gets a
    conflict and must not append a duplicate user message (#91)."""
    service, _bus, register, workspace_id, user_id = chat
    register(WAIT_CANCEL_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.send_message(session["id"], workspace_id, "long turn")
    _wait_for(lambda: service.get_session(session["id"])["status"] == "running")

    with pytest.raises(ConflictError):
        service.send_message(session["id"], workspace_id, "second")

    user_texts = [
        m["content"]["text"]
        for m in service.list_messages(session["id"], workspace_id)
        if m["kind"] == "text" and m["role"] == "user"
    ]
    assert user_texts == ["long turn"]


def test_unanswered_permission_auto_denies_after_timeout(chat, monkeypatch) -> None:
    """A permission prompt the human never answers (closed browser) must not
    park the waiter thread forever: the timeout auto-denies it (#91)."""
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(HUMAN_PERMISSION_SCRIPT)
    monkeypatch.setattr(permissions_module, "PERMISSION_TIMEOUT_SECONDS", 0.2)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.send_message(session["id"], workspace_id, "run ls")

    _wait_for(lambda: service.get_session(session["id"])["status"] == "awaiting_permission")
    _wait_for(lambda: service.get_session(session["id"])["status"] == "idle")
    decisions = [
        m["content"]["decision"]
        for m in service.list_messages(session["id"], workspace_id)
        if m["kind"] == "permission" and m["content"].get("status") == "resolved"
    ]
    assert decisions and decisions[-1] == {"deny": True, "via": "timeout"}
    outcomes = [
        e["permission_outcome"] for e in _read_sink(script_path) if "permission_outcome" in e
    ]
    assert outcomes == [{"outcome": "cancelled"}]


def test_close_session_records_closed_at(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    register(TEXT_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")

    closed = service.close_session(session["id"], workspace_id)

    assert closed["status"] == "closed"
    assert closed["closed_at"] is not None


def test_handle_cancel_after_loop_closed_is_a_noop() -> None:
    """cancel() racing a torn-down session loop must not raise the closed-loop
    RuntimeError back into the request path (#91)."""

    class _NoopCallbacks:
        def on_ready(self, capabilities, acp_session_id) -> None: ...

        def on_update(self, update) -> None: ...

        def on_permission_request(self, tool_call, options) -> dict:
            return {"deny": True}

        def on_turn_end(self, stop_reason) -> None: ...

        def on_turn_error(self, detail) -> None: ...

        def on_error(self, detail) -> None: ...

        def on_exit(self) -> None: ...

    handle = AcpSessionHandle(
        command=sys.executable,
        args=[],
        cwd=".",
        mcp_server=HttpMcpServer(type="http", name="x", url="http://x/mcp", headers=[]),
        env=None,
        callbacks=_NoopCallbacks(),
    )
    closed_loop = asyncio.new_event_loop()
    closed_loop.close()
    handle._loop = closed_loop
    handle._conn = object()
    handle._acp_session_id = "acp-1"

    handle.cancel()


class _StubHandle:
    """Minimal ACP handle stand-in for tests that drive the service callbacks
    directly (no subprocess) to control interleaving precisely (#98)."""

    def send_prompt(self, text: str) -> bool:
        del text
        return True

    def cancel(self) -> None: ...

    def close(self) -> None: ...


def _direct_session(job_db, settings):
    """Idle session row + registered runtime without an ACP subprocess."""
    bus = RecordingBus()
    service = StudioChatService(job_db, settings, bus)
    workspace_id = job_db.create_workspace(default_workflow_key="demo_workflow", name="Chat WS")[
        "id"
    ]
    user_id = str(job_db.create_user("chat-user", password_hash=None)["id"])
    session_id = job_db.create_studio_chat_session(workspace_id, user_id, "direct-agent")
    job_db.update_studio_chat_session(session_id, status="idle")
    runtime = SessionRuntime(_StubHandle(), token="direct-token")
    with service._runtimes_lock:
        service._runtimes[session_id] = runtime
    return service, session_id, runtime, workspace_id


def _chunk(text: str) -> dict:
    return {
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": text},
    }


def _agent_texts(service, session_id: str, workspace_id: str) -> list[str]:
    return [
        m["content"]["text"]
        for m in service.list_messages(session_id, workspace_id)
        if m["kind"] == "text" and m["role"] == "agent"
    ]


def test_trailing_chunk_after_turn_end_folds_into_finished_turn_row(job_db, settings) -> None:
    """#98: the ACP SDK can deliver trailing chunks after turn_end. They must
    keep folding into the finished turn's row — no tail-only orphan row — and
    the next turn's first chunk must get its own row (slots reset at turn
    start), never overwrite the previous turn's row in place."""
    service, session_id, _runtime, workspace_id = _direct_session(job_db, settings)
    try:
        service.send_message(session_id, workspace_id, "first")
        service._on_update(session_id, _chunk("Hello"))
        service._on_turn_end(session_id, "end_turn")

        # Trailing chunk of the finished turn arriving after turn end.
        service._on_update(session_id, _chunk(" world"))

        service.send_message(session_id, workspace_id, "second")
        service._on_update(session_id, _chunk("Second"))
        service._on_turn_end(session_id, "end_turn")

        assert _agent_texts(service, session_id, workspace_id) == ["Hello world", "Second"]
    finally:
        service.shutdown()


def test_turn_start_reset_cannot_land_between_stream_create_and_attach(
    job_db, settings, monkeypatch
) -> None:
    """#98 regression: the first-chunk create+attach is one critical section.
    A turn-start reset landing between them used to leave a stale open id, so
    the next turn's first chunk updated the previous turn's row in place."""
    service, session_id, runtime, workspace_id = _direct_session(job_db, settings)
    try:
        service.send_message(session_id, workspace_id, "first")
        real_append = job_db.append_studio_chat_message
        create_started = threading.Event()
        proceed = threading.Event()
        reset_at_lock = threading.Event()
        errors: list[BaseException] = []

        def blocking_append(*args, **kwargs):
            create_started.set()
            if not proceed.wait(timeout=10):
                raise RuntimeError("test deadlock: create never released")
            return real_append(*args, **kwargs)

        def run_chunk() -> None:
            try:
                service._on_update(session_id, _chunk("Hello"))
            except BaseException as exc:  # surfaced after join
                errors.append(exc)

        def turn_start_reset() -> None:
            try:
                # Signal right before contending for the runtime lock: the
                # create thread already holds it (create_started), so this
                # marks the reset attempt about to block. "Already blocked"
                # is not observable without instrumenting the lock; the gap
                # between this set and the acquire is a handful of bytecode
                # ops, and even a preemption there still yields the correct
                # order (reset after attach) — the event only guarantees the
                # race is genuinely exercised, without a fixed sleep.
                reset_at_lock.set()
                with runtime.lock:
                    runtime.stream.reset()
            except BaseException as exc:  # surfaced after join
                errors.append(exc)

        monkeypatch.setattr(job_db, "append_studio_chat_message", blocking_append)
        chunk_thread = threading.Thread(target=run_chunk)
        chunk_thread.start()
        assert create_started.wait(timeout=10)

        # Races the in-flight create: must block on the runtime lock until
        # create+attach finished, then clear the freshly attached id.
        reset_thread = threading.Thread(target=turn_start_reset)
        reset_thread.start()
        assert reset_at_lock.wait(timeout=10)
        proceed.set()
        chunk_thread.join(timeout=10)
        reset_thread.join(timeout=10)
        assert not chunk_thread.is_alive() and not reset_thread.is_alive()
        assert not errors

        # The next turn's first chunk must create its own row; the previous
        # turn's row keeps its text.
        service._on_update(session_id, _chunk("Second"))

        assert _agent_texts(service, session_id, workspace_id) == ["Hello", "Second"]
    finally:
        service.shutdown()
