"""Shared Studio ACP fixtures and Kimi task metadata builders."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from server.app.auth.scoped_tokens import mint_scoped_token
from server.app.studio_chat.acp_session import AcpSessionHandle
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

    def publish(self, channel: str, payload: str, *, replaceable: bool = False) -> None:
        self.events.append((channel, json.loads(payload)))

    def subscribe(self, channel: str):
        raise NotImplementedError

    def unsubscribe(self, channel: str, queue) -> None:
        del channel, queue


@pytest.fixture
def chat(job_db, settings, tmp_path, monkeypatch):
    # #915: the registered api_base (127.0.0.1:8000) is never this test
    # process; the callback self-check is pinned to "reachable" here so the
    # timeline assertions stay about their own subject. The check itself is
    # covered by tests/services/test_studio_chat_callback_check.py.
    monkeypatch.setattr("server.app.studio_chat.spawn.check_api_base", lambda api_base: None)
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


@pytest.fixture
def admission(job_db, settings):
    service = StudioChatService(job_db, settings, None)
    workspace = job_db.create_workspace(default_workflow_key="demo_workflow", name="Admission")[
        "id"
    ]
    user = job_db.create_user("admission-user", password_hash=None)["id"]
    sid = job_db.create_studio_chat_session(workspace, user, "test-agent")
    job_db.update_studio_chat_session(sid, status="idle")
    handle = AcpSessionHandle(
        command="unused", args=[], cwd="/tmp", mcp_server=None, env=None, callbacks=Mock()
    )
    runtime = SessionRuntime(handle, mint_scoped_token(job_db, user, workspace_id=workspace))
    runtime.loading = True
    runtime.resume_transcript_pending = True
    runtime.stream.append("text", "previous")
    service._runtimes[sid] = runtime
    yield service, job_db, sid, workspace, runtime
    service.shutdown()


def write_task(root, task_id="agent-1", status="running", **spec_overrides):
    path = root / task_id
    path.mkdir(parents=True, exist_ok=True)
    spec = {
        "version": 1,
        "id": task_id,
        "session_id": "acp-1",
        "kind": "agent",
        "owner_role": "root",
        **spec_overrides,
    }
    (path / "spec.json").write_text(json.dumps(spec))
    (path / "runtime.json").write_text(json.dumps({"status": status}))
    return path
