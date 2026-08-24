"""Studio chat lifecycle hardening tests (#158 review follow-ups).

Covers the fixes tracked in issue #158: the active-session spawn cap, token
renewal at turn start, agent-death teardown (runtime + token), the permission
park/settle protocol against close and double-respond races, the guarded
session status UPDATE, and the synchronous kill fallback for a wedged ACP
loop. The session-lifecycle happy paths live in
tests/services/test_studio_chat_service.py.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest
from acp.schema import HttpMcpServer

from server.app.auth.scoped_tokens import authenticate_scoped_token
from server.app.auth.sessions import hash_token
from server.app.services.job_errors import ConflictError, NotFoundError
from server.app.studio_chat import service as service_module
from server.app.studio_chat.acp_session import AcpSessionHandle
from server.app.studio_chat.registry import StudioAgentRegistryStore
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


def _session_token(script_path: Path) -> str:
    for entry in _read_sink(script_path):
        message = entry.get("received", {})
        if message.get("method") == "session/new":
            server = message["params"]["mcpServers"][0]
            headers = {item["name"]: item["value"] for item in server.get("headers", [])}
            return headers["Authorization"].removeprefix("Bearer ")
    raise AssertionError("session/new never reached the fake agent")


def test_create_session_rejected_beyond_active_cap(chat, monkeypatch) -> None:
    service, _bus, register, workspace_id, user_id = chat
    register(TEXT_SCRIPT)
    monkeypatch.setattr(service_module, "MAX_ACTIVE_STUDIO_CHAT_SESSIONS", 1)
    service.create_session(workspace_id, user_id, "fake-agent")
    with pytest.raises(ConflictError, match="Too many active studio chat sessions"):
        service.create_session(workspace_id, user_id, "fake-agent")


def test_turn_start_renews_expiring_scoped_token(chat, job_db) -> None:
    """A token close to expiry is slid forward a full TTL at turn start (#3)."""
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(TEXT_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    token = _session_token(script_path)
    with job_db.connect() as conn:
        conn.execute(
            "update auth_scoped_tokens"
            " set expires_at = current_timestamp + interval '1 minute'"
            " where token_hash=%s",
            (hash_token(token),),
        )
    service.send_message(session["id"], workspace_id, "hi")
    with job_db.connect() as conn:
        row = conn.execute(
            "select expires_at > current_timestamp + interval '1 hour' as renewed"
            " from auth_scoped_tokens where token_hash=%s",
            (hash_token(token),),
        ).fetchone()
    assert row["renewed"]
    _wait_for(lambda: service.get_session(session["id"])["status"] == "idle")


def test_fresh_token_is_not_rewritten_at_turn_start(chat, job_db) -> None:
    """A token with more life than the renew threshold is left untouched."""
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(TEXT_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    token = _session_token(script_path)
    service.send_message(session["id"], workspace_id, "hi")
    with job_db.connect() as conn:
        row = conn.execute(
            "select expires_at <= current_timestamp + interval '2 hours' as untouched"
            " from auth_scoped_tokens where token_hash=%s",
            (hash_token(token),),
        ).fetchone()
    assert row["untouched"]
    _wait_for(lambda: service.get_session(session["id"])["status"] == "idle")


def test_agent_exit_tears_down_runtime_and_revokes_token(chat, job_db) -> None:
    """Agent death pops the runtime and revokes the token instead of relying
    on the TTL/timeout backstops (#4)."""
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(TEXT_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    token = _session_token(script_path)
    runtime = service._runtime(session["id"])
    assert runtime is not None
    service._on_exit(session["id"])
    assert service._runtime(session["id"]) is None
    assert authenticate_scoped_token(job_db, token) is None
    assert service.get_session(session["id"])["status"] == "error"
    # _on_exit skips the handle by design (it runs on the ACP thread); reap
    # the fake agent here so the test does not leak the subprocess.
    runtime.handle.close()


def test_close_during_permission_park_leaves_session_closed(chat) -> None:
    """The parked thread settled by a close must not resurrect the session
    back to running (#1)."""
    service, _bus, register, workspace_id, user_id = chat
    register(HUMAN_PERMISSION_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.send_message(session["id"], workspace_id, "run ls")
    _wait_for(lambda: service.get_session(session["id"])["status"] == "awaiting_permission")
    service.close_session(session["id"], workspace_id)
    # The resolved message is appended after the parked thread's finally
    # block, so observing it proves the status write already happened.
    _wait_for(
        lambda: any(
            m["kind"] == "permission" and m["content"].get("status") == "resolved"
            for m in service.list_messages(session["id"], workspace_id)
        )
    )
    time.sleep(0.2)
    assert service.get_session(session["id"])["status"] == "closed"


def test_respond_permission_after_settle_is_not_found(chat) -> None:
    """A settled request is popped: a second respond 404s instead of writing
    an orphan decision (#7)."""
    service, _bus, register, workspace_id, user_id = chat
    register(HUMAN_PERMISSION_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.send_message(session["id"], workspace_id, "run ls")
    _wait_for(lambda: service.get_session(session["id"])["status"] == "awaiting_permission")
    request_id = next(
        m["content"]["request_id"]
        for m in service.list_messages(session["id"], workspace_id)
        if m["kind"] == "permission" and m["content"].get("status") == "pending"
    )
    service.respond_permission(
        session["id"], workspace_id, request_id, option_id="allow", deny=False
    )
    _wait_for(lambda: service.get_session(session["id"])["status"] == "idle")
    with pytest.raises(NotFoundError):
        service.respond_permission(
            session["id"], workspace_id, request_id, option_id="allow", deny=False
        )


def test_permission_park_after_teardown_denies_immediately(chat) -> None:
    """A request parking against a torn-down runtime denies at once instead
    of hanging to the 120s timeout (#6)."""
    service, _bus, register, workspace_id, user_id = chat
    register(TEXT_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    runtime = service._runtime(session["id"])
    assert runtime is not None
    with runtime.lock:
        runtime.closed = True
    decision = service._on_permission_request(session["id"], {"title": "Bash: rm"}, [])
    assert decision == {"deny": True}
    assert not any(
        m["kind"] == "permission" for m in service.list_messages(session["id"], workspace_id)
    )


def test_guarded_session_update_respects_status_predicate(chat, job_db) -> None:
    """The atomic check-and-set behind all lifecycle writes (#1/#5)."""
    _service, _bus, _register, workspace_id, user_id = chat
    session_id = job_db.create_studio_chat_session(workspace_id, user_id, "agent-x")
    assert not job_db.update_studio_chat_session_if(
        session_id, status_in=("running",), status="awaiting_permission"
    )
    assert job_db.get_studio_chat_session(session_id)["status"] == "starting"
    assert job_db.update_studio_chat_session_if(
        session_id, status_not_in=("closed",), status="running"
    )
    assert job_db.get_studio_chat_session(session_id)["status"] == "running"
    assert not job_db.update_studio_chat_session_if(
        session_id, status_not_in=("running",), status="closed"
    )
    assert job_db.get_studio_chat_session(session_id)["status"] == "running"


class _NullCallbacks:
    def on_ready(self, capabilities, acp_session_id) -> None: ...
    def on_update(self, update) -> None: ...
    def on_permission_request(self, tool_call, options) -> dict:
        return {"deny": True}

    def on_turn_end(self, stop_reason) -> None: ...
    def on_turn_error(self, detail) -> None: ...
    def on_error(self, detail) -> None: ...
    def on_exit(self) -> None: ...


class _FakeProcess:
    def __init__(self) -> None:
        self.returncode = None
        self.killed = threading.Event()

    def kill(self) -> None:
        self.killed.set()


def test_kill_process_signals_directly_without_the_loop() -> None:
    """The escalation path must not depend on the (possibly wedged) event
    loop: process.kill is issued synchronously (#10)."""
    handle = AcpSessionHandle(
        command="fake",
        args=[],
        cwd=".",
        mcp_server=HttpMcpServer(
            type="http", name="fake", url="http://127.0.0.1:9/mcp", headers=[]
        ),
        env=None,
        callbacks=_NullCallbacks(),
    )
    process = _FakeProcess()
    handle._process = process
    # An object without call_soon_threadsafe: any loop-mediated kill path
    # would blow up here, proving the kill went out synchronously.
    handle._loop = object()
    handle._kill_process()
    assert process.killed.is_set()

    # A reaped child is never re-killed.
    reaped = _FakeProcess()
    reaped.returncode = 0
    handle._process = reaped
    handle._kill_process()
    assert not reaped.killed.is_set()
