"""Studio chat session teardown, failure paths and turn cancellation.

Split from tests/services/test_studio_chat_service.py to stay clear of the
test-file line budget (#207); session/turn streaming lives in
test_studio_chat_service_sessions.py and permission flows in
test_studio_chat_service_permissions.py. Shared scripts, the RecordingBus and
the ``chat`` fixture are duplicated per sibling (each file registers its own
fake-agent scripts), matching the convention of the workers suite split.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest
from acp.schema import HttpMcpServer

from server.app.auth.scoped_tokens import authenticate_scoped_token
from server.app.services.job_errors import ConflictError, InvalidOperationError
from server.app.studio_chat import spawn as spawn_module
from server.app.studio_chat.acp_session import AcpSessionHandle
from server.app.studio_chat.registry import StudioAgentRegistryStore
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


def test_close_during_pending_permission_stays_closed(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    register(HUMAN_PERMISSION_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.send_message(session["id"], workspace_id, "run ls")
    _wait_for(lambda: service.get_session(session["id"])["status"] == "awaiting_permission")
    runtime = service.runtime(session["id"])
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
    real_mint = spawn_module.mint_scoped_token

    def capturing_mint(*args, **kwargs):
        token = real_mint(*args, **kwargs)
        minted.append(token)
        return token

    def exploding_handle(**kwargs):
        raise RuntimeError("spawn blew up")

    monkeypatch.setattr(spawn_module, "mint_scoped_token", capturing_mint)
    monkeypatch.setattr(spawn_module, "AcpSessionHandle", exploding_handle)

    with pytest.raises(RuntimeError, match="spawn blew up"):
        service.create_session(workspace_id, user_id, "fake-agent")

    sessions = service.list_sessions(workspace_id)
    assert sessions and sessions[0]["status"] == "error"
    assert minted and authenticate_scoped_token(job_db, minted[0]) is None
    assert service.runtime(sessions[0]["id"]) is None


def test_create_session_mint_failure_still_clears_starting_row(chat, monkeypatch) -> None:
    """A failure inside token minting (before the handle exists) must also
    funnel through the cleanup path: no 'starting' residue, no revoke of a
    token that never materialized (#91 review follow-up)."""
    service, _bus, register, workspace_id, user_id = chat
    register(TEXT_SCRIPT)

    def exploding_mint(*args, **kwargs):
        raise RuntimeError("mint blew up")

    monkeypatch.setattr(spawn_module, "mint_scoped_token", exploding_mint)

    with pytest.raises(RuntimeError, match="mint blew up"):
        service.create_session(workspace_id, user_id, "fake-agent")

    sessions = service.list_sessions(workspace_id)
    assert sessions and sessions[0]["status"] == "error"
    assert service.runtime(sessions[0]["id"]) is None


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

        def on_exit(self, *, close_initiated: bool) -> None: ...

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
