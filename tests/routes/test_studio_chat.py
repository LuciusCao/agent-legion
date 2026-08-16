"""Studio chat route contract tests (phase 3 chunk 4, ACP conversation API)."""

from __future__ import annotations

import contextlib
import json
import sys
import time
from pathlib import Path

import pytest

FAKE_AGENT = Path(__file__).resolve().parents[1] / "helpers" / "fake_acp_agent.py"
CSRF = {"x-agent-legion-request": "1"}

# Sessions created through _create_session during the current test, drained by
# the autouse cleanup below.
_CREATED_SESSIONS: list[tuple[str, str]] = []


@pytest.fixture(autouse=True)
def _close_created_sessions(client):
    """Per-test backstop: a mid-test assertion failure must not orphan the
    fake ACP subprocess on the shared app (#91)."""
    _CREATED_SESSIONS.clear()
    yield
    for workspace_id, session_id in _CREATED_SESSIONS:
        # Best-effort: the test's own failure is the signal that matters.
        with contextlib.suppress(Exception):
            client.delete(_session_url(workspace_id, session_id))
    _CREATED_SESSIONS.clear()


ECHO_SCRIPT = {
    "on_prompt": [
        {
            "notify": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "pong"},
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


def _wait_for(condition, timeout: float = 20.0, interval: float = 0.05) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(interval)
    raise AssertionError("condition not met within timeout")


def _register_fake_agent(
    client, tmp_path, script: dict | None = None, agent_id="fake-agent"
) -> Path:
    script_path = tmp_path / f"{agent_id}-script.json"
    script_path.write_text(json.dumps(script if script is not None else ECHO_SCRIPT))
    response = client.put(
        "/api/admin/studio-agents",
        json={
            "api_base": "http://127.0.0.1:8000",
            "agents": [
                {
                    "id": agent_id,
                    "label": "Fake Agent",
                    "command": sys.executable,
                    "args": [str(FAKE_AGENT), str(script_path)],
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    return script_path


def _create_workspace(client, name="Chat WS") -> str:
    response = client.post(
        "/api/workspaces",
        json={"name": name, "default_workflow_key": "education_video_problems_generation"},
    )
    assert response.status_code == 200, response.text
    return response.json()["workspace"]["id"]


def _create_session(client, workspace_id: str) -> str:
    response = client.post(
        f"/api/workspaces/{workspace_id}/studio-chat/sessions",
        json={"agent_id": "fake-agent", "title": "t"},
    )
    assert response.status_code == 200, response.text
    session_id = response.json()["session"]["id"]
    _CREATED_SESSIONS.append((workspace_id, session_id))
    return session_id


def _session_url(workspace_id: str, session_id: str) -> str:
    return f"/api/workspaces/{workspace_id}/studio-chat/sessions/{session_id}"


def _member_client(client, username="chat-member", password="pw1"):
    response = client.post("/api/users", json={"username": username, "password": password})
    assert response.status_code == 201, response.text
    member_id = response.json()["id"]
    member = client.__class__(client.app)
    response = member.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    member.headers["x-agent-legion-request"] = "1"
    return member, member_id


def test_anonymous_requests_return_401(anon_client) -> None:
    base = "/api/workspaces/ws-1/studio-chat"
    assert anon_client.get(f"{base}/agents").status_code == 401
    assert anon_client.get(f"{base}/sessions").status_code == 401
    assert anon_client.post(f"{base}/sessions", json={"agent_id": "x"}).status_code == 401
    assert anon_client.get(f"{base}/sessions/s-1").status_code == 401
    assert anon_client.get(f"{base}/sessions/s-1/messages").status_code == 401
    assert anon_client.post(f"{base}/sessions/s-1/messages", json={"text": "hi"}).status_code == 401
    assert anon_client.post(f"{base}/sessions/s-1/cancel").status_code == 401
    assert anon_client.delete(f"{base}/sessions/s-1").status_code == 401
    assert (
        anon_client.post(f"{base}/sessions/s-1/permissions/r-1", json={"deny": True}).status_code
        == 401
    )


def test_non_member_gets_404(client, job_db, tmp_path) -> None:
    _register_fake_agent(client, tmp_path)
    workspace_id = _create_workspace(client)
    member, _ = _member_client(client)
    assert member.get(f"/api/workspaces/{workspace_id}/studio-chat/sessions").status_code == 404
    assert (
        member.post(
            f"/api/workspaces/{workspace_id}/studio-chat/sessions",
            json={"agent_id": "fake-agent"},
        ).status_code
        == 404
    )


def test_viewer_reads_but_cannot_write(client, job_db, tmp_path) -> None:
    _register_fake_agent(client, tmp_path)
    workspace_id = _create_workspace(client)
    session_id = _create_session(client, workspace_id)
    member, member_id = _member_client(client)
    job_db.upsert_workspace_member(workspace_id, member_id, "viewer")
    try:
        base = f"/api/workspaces/{workspace_id}/studio-chat"
        assert member.get(f"{base}/sessions").status_code == 200
        assert member.get(f"{base}/sessions/{session_id}").status_code == 200
        assert member.get(f"{base}/sessions/{session_id}/messages").status_code == 200
        assert member.get(f"{base}/agents").status_code == 200
        assert (
            member.post(f"{base}/sessions/{session_id}/messages", json={"text": "hi"}).status_code
            == 403
        )
        assert member.post(f"{base}/sessions/{session_id}/cancel").status_code == 403
        assert (
            member.post(
                f"{base}/sessions/{session_id}/permissions/allow-all", json={"enabled": True}
            ).status_code
            == 403
        )
    finally:
        client.delete(_session_url(workspace_id, session_id))


def test_agents_endpoint_hides_command_lines(client, tmp_path) -> None:
    _register_fake_agent(client, tmp_path)
    workspace_id = _create_workspace(client)
    response = client.get(f"/api/workspaces/{workspace_id}/studio-chat/agents")
    assert response.status_code == 200
    agents = response.json()["agents"]
    assert agents == [{"id": "fake-agent", "label": "Fake Agent"}]
    assert sys.executable not in json.dumps(response.json())


def test_full_conversation_flow_and_token_hygiene(client, tmp_path) -> None:
    script_path = _register_fake_agent(client, tmp_path)
    workspace_id = _create_workspace(client)
    session_id = _create_session(client, workspace_id)
    url = _session_url(workspace_id, session_id)

    detail = client.get(url).json()["session"]
    assert detail["status"] == "idle"
    assert detail["title"] == "t"
    assert detail["acp_session_id"] == "fake-session-1"

    response = client.post(f"{url}/messages", json={"text": "ping"})
    assert response.status_code == 200, response.text
    assert response.json()["message"]["role"] == "user"

    def turn_finished() -> bool:
        messages = client.get(f"{url}/messages").json()["messages"]
        return any(m["kind"] == "text" and m["role"] == "agent" for m in messages)

    _wait_for(turn_finished)
    messages = client.get(f"{url}/messages").json()["messages"]
    agent_text = next(m for m in messages if m["kind"] == "text" and m["role"] == "agent")
    assert agent_text["content"]["text"] == "pong"
    # The turn never touched an agent-legion MCP tool: run flagged, not silent.
    assert client.get(url).json()["session"]["mcp_status"] == "unverified"

    # Token hygiene: the injected scoped token works against the tool surface
    # while the session lives, appears in no API payload, and dies on close.
    sink = [
        json.loads(line) for line in Path(str(script_path) + ".sink.jsonl").read_text().splitlines()
    ]
    new_session = next(
        e["received"] for e in sink if e.get("received", {}).get("method") == "session/new"
    )
    env = {item["name"]: item["value"] for item in new_session["params"]["mcpServers"][0]["env"]}
    token = env["AGENT_LEGION_STUDIO_AGENT_TOKEN"]
    assert env["AGENT_LEGION_MCP_API_BASE"] == "http://127.0.0.1:8000"
    tools = client.get(
        "/api/studio-agent/tools/workflows", headers={"Authorization": f"Bearer {token}"}
    )
    assert tools.status_code == 200
    assert token not in json.dumps(client.get(f"{url}/messages").json())
    assert token not in json.dumps(client.get(url).json())

    closed = client.delete(url)
    assert closed.status_code == 200
    assert closed.json()["session"]["status"] == "closed"
    revoked = client.get(
        "/api/studio-agent/tools/workflows", headers={"Authorization": f"Bearer {token}"}
    )
    assert revoked.status_code == 401


def test_permission_forward_and_answer_via_api(client, tmp_path) -> None:
    _register_fake_agent(client, tmp_path, script=HUMAN_PERMISSION_SCRIPT)
    workspace_id = _create_workspace(client)
    session_id = _create_session(client, workspace_id)
    url = _session_url(workspace_id, session_id)

    client.post(f"{url}/messages", json={"text": "run ls"})
    _wait_for(lambda: client.get(url).json()["session"]["status"] == "awaiting_permission")

    pending = next(
        m
        for m in client.get(f"{url}/messages").json()["messages"]
        if m["kind"] == "permission" and m["content"].get("status") == "pending"
    )
    request_id = pending["content"]["request_id"]
    assert pending["content"]["tool_call"]["title"] == "Bash: ls"
    assert [o["optionId"] for o in pending["content"]["options"]] == ["allow", "deny"]

    resolved = client.post(f"{url}/permissions/{request_id}", json={"option_id": "allow"})
    assert resolved.status_code == 200, resolved.text
    _wait_for(lambda: client.get(url).json()["session"]["status"] == "idle")
    decisions = [
        m["content"]["decision"]
        for m in client.get(f"{url}/messages").json()["messages"]
        if m["kind"] == "permission" and m["content"].get("status") == "resolved"
    ]
    assert decisions and decisions[-1].get("option_id") == "allow"
    client.delete(url)


def test_permission_answer_requires_option_or_deny(client, tmp_path) -> None:
    _register_fake_agent(client, tmp_path)
    workspace_id = _create_workspace(client)
    session_id = _create_session(client, workspace_id)
    url = _session_url(workspace_id, session_id)
    response = client.post(f"{url}/permissions/r-x", json={})
    assert response.status_code == 422
    response = client.post(f"{url}/permissions/r-x", json={"option_id": "allow"})
    assert response.status_code == 404
    client.delete(url)


def test_cross_workspace_session_is_not_found(client, tmp_path) -> None:
    _register_fake_agent(client, tmp_path)
    workspace_a = _create_workspace(client, "WS A")
    workspace_b = _create_workspace(client, "WS B")
    session_id = _create_session(client, workspace_a)
    try:
        assert client.get(_session_url(workspace_b, session_id)).status_code == 404
        assert (
            client.post(
                _session_url(workspace_b, session_id) + "/messages", json={"text": "hi"}
            ).status_code
            == 404
        )
    finally:
        client.delete(_session_url(workspace_a, session_id))


def test_create_session_with_unknown_agent_is_400(client, tmp_path) -> None:
    _register_fake_agent(client, tmp_path)
    workspace_id = _create_workspace(client)
    response = client.post(
        f"/api/workspaces/{workspace_id}/studio-chat/sessions", json={"agent_id": "nope"}
    )
    assert response.status_code == 400
