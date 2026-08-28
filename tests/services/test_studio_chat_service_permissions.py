"""Studio chat permission flows and the client-side terminal protocol.

Split from tests/services/test_studio_chat_service.py to stay clear of the
test-file line budget (#207); session/turn streaming lives in
test_studio_chat_service_sessions.py and teardown/failure cases in
test_studio_chat_service_lifecycle.py. Shared scripts, the RecordingBus and
the ``chat`` fixture are duplicated per sibling (each file registers its own
fake-agent scripts), matching the convention of the workers suite split.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from server.app.studio_chat import permissions as permissions_module
from server.app.studio_chat.registry import StudioAgentRegistryStore
from server.app.studio_chat.service import StudioChatService
from tests.helpers import wait_for_predicate
from tests.postgres_support import TEST_DATABASE_URL

FAKE_AGENT = Path(__file__).resolve().parents[1] / "helpers" / "fake_acp_agent.py"

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


TERMINAL_SCRIPT = {
    "on_prompt": [
        {
            "terminal": {
                "command": sys.executable,
                "args": ["-c", "print('terminal says hi')"],
            }
        }
    ]
}


def test_initialize_advertises_terminal_capability(chat) -> None:
    """kimi's Bash/Grep tools only run when the client advertises
    clientCapabilities.terminal=true at initialize."""
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(TERMINAL_SCRIPT)
    service.create_session(workspace_id, user_id, "fake-agent")

    sink = _read_sink(script_path)
    initialize = next(e["initialize_params"] for e in sink if "initialize_params" in e)
    assert initialize["clientCapabilities"]["terminal"] is True


def test_terminal_roundtrip_runs_command_and_returns_output(chat) -> None:
    """terminal/create → wait_for_exit → output → release drives a real
    subprocess through the client-side terminal protocol."""
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(TERMINAL_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.send_message(session["id"], workspace_id, "run a command")

    _wait_for(lambda: service.get_session(session["id"])["status"] == "idle")
    outcomes = [e["terminal_outcome"] for e in _read_sink(script_path) if "terminal_outcome" in e]
    assert outcomes and outcomes[0]["exitCode"] == 0
    assert "terminal says hi" in outcomes[0]["output"]


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
