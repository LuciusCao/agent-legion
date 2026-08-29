"""Studio chat session lifecycle: creation, turn streaming, and token minting.

Split from tests/services/test_studio_chat_service.py to stay clear of the
test-file line budget (#207); permission-flow and teardown/failure cases live
in the sibling files test_studio_chat_service_permissions.py and
test_studio_chat_service_lifecycle.py. Shared scripts, the RecordingBus and
the ``chat`` fixture are duplicated per sibling (each file registers its own
fake-agent scripts), matching the convention of the workers suite split.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

from server.app.auth.scoped_tokens import authenticate_scoped_token
from server.app.services.job_errors import ConflictError, NotFoundError
from server.app.studio_chat.prompts import STUDIO_AUTHORING_BOOTSTRAP
from server.app.studio_chat.registry import StudioAgentRegistryStore
from server.app.studio_chat.runtime import SessionRuntime
from server.app.studio_chat.service import StudioChatService
from tests.helpers import wait_for_predicate
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


def test_set_draft_yaml_persists_on_session_row(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    register(TEXT_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")

    updated = service.set_draft_yaml(session["id"], workspace_id, "key: wf\n")
    assert updated["draft_yaml"] == "key: wf\n"
    assert service.get_session(session["id"])["draft_yaml"] == "key: wf\n"
    with pytest.raises(NotFoundError):
        service.set_draft_yaml(session["id"], "other-ws", "key: wf\n")


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
