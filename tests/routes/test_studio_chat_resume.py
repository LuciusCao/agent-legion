"""Studio chat resume route tests: POST .../sessions/{id}/resume."""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

import pytest

from tests.helpers import wait_for_predicate

FAKE_AGENT = Path(__file__).resolve().parents[1] / "helpers" / "fake_acp_agent.py"

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


def _wait_for(condition, timeout: float = 20.0, interval: float = 0.05) -> None:
    wait_for_predicate(condition, timeout=timeout, interval=interval)


def _register_fake_agent(client, tmp_path, script: dict | None = None) -> Path:
    script_path = tmp_path / "fake-agent-script.json"
    script_path.write_text(json.dumps(script if script is not None else ECHO_SCRIPT))
    response = client.put(
        "/api/admin/studio-agents",
        json={
            "api_base": "http://127.0.0.1:8000",
            "agents": [
                {
                    "id": "fake-agent",
                    "label": "Fake Agent",
                    "command": sys.executable,
                    "args": [str(FAKE_AGENT), str(script_path)],
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    return script_path


_CREATE_COUNT = 0


def _create_workspace(client, name="Chat WS") -> str:
    # v62: id==key and unique per call within a test (TRUNCATE isolation
    # resets the counter each test).
    global _CREATE_COUNT
    _CREATE_COUNT += 1
    ws_id = (
        "education_video_problems_generation"
        if _CREATE_COUNT == 1
        else f"education_video_problems_generation_{_CREATE_COUNT}"
    )
    response = client.post(
        "/api/workspaces",
        json={"id": ws_id, "name": name},
    )
    assert response.status_code == 200, response.text
    return response.json()["workspace"]["id"]


@pytest.fixture(autouse=True)
def _reset_create_count():
    global _CREATE_COUNT
    _CREATE_COUNT = 0
    yield


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


def test_resume_route_roundtrip_restores_conversation(client, tmp_path) -> None:
    script_path = _register_fake_agent(client, tmp_path)
    workspace_id = _create_workspace(client)
    session_id = _create_session(client, workspace_id)
    url = _session_url(workspace_id, session_id)

    client.post(f"{url}/messages", json={"text": "ping"})
    _wait_for(lambda: client.get(url).json()["session"]["status"] == "idle")
    closed = client.delete(url)
    assert closed.status_code == 200
    assert closed.json()["session"]["status"] == "closed"
    # closed 会话发送 fail-closed。
    assert client.post(f"{url}/messages", json={"text": "x"}).status_code == 409

    resumed = client.post(f"{url}/resume")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["session"]["status"] == "idle"

    # 恢复后可以继续对话；首条 prompt 注入此前对话转录。
    assert client.post(f"{url}/messages", json={"text": "continue"}).status_code == 200
    _wait_for(lambda: client.get(url).json()["session"]["status"] == "idle")
    sink = [
        json.loads(line) for line in Path(str(script_path) + ".sink.jsonl").read_text().splitlines()
    ]
    prompts = [
        block["text"]
        for entry in sink
        if entry.get("received", {}).get("method") == "session/prompt"
        for block in entry["received"]["params"].get("prompt", [])
    ]
    assert "此前对话的记录" in prompts[-1]
    assert "用户：ping" in prompts[-1]
    messages = client.get(f"{url}/messages").json()["messages"]
    assert any(m["kind"] == "text" and m["content"].get("text") == "ping" for m in messages)
    client.delete(url)


def test_resume_live_session_is_noop(client, tmp_path) -> None:
    _register_fake_agent(client, tmp_path)
    workspace_id = _create_workspace(client)
    session_id = _create_session(client, workspace_id)
    url = _session_url(workspace_id, session_id)

    response = client.post(f"{url}/resume")
    assert response.status_code == 200, response.text
    assert response.json()["session"]["status"] == "idle"
    client.delete(url)


def test_resume_auth_boundaries(client, job_db, tmp_path) -> None:
    """Anonymous 401, non-member 404, non-admin member 403, cross-workspace
    404, and the session's own scoped token (studio_agent) 403."""
    script_path = _register_fake_agent(client, tmp_path)
    workspace_a = _create_workspace(client, "WS A")
    workspace_b = _create_workspace(client, "WS B")
    session_id = _create_session(client, workspace_a)
    url_a = _session_url(workspace_a, session_id)
    try:
        assert client.post(f"{url_a}/resume").status_code == 200

        member, member_id = _member_client(client)
        assert member.post(f"{url_a}/resume").status_code == 404
        job_db.upsert_workspace_member(workspace_a, member_id, "editor")
        assert member.post(f"{url_a}/resume").status_code == 403

        assert client.post(f"{_session_url(workspace_b, session_id)}/resume").status_code == 404

        sink = [
            json.loads(line)
            for line in Path(str(script_path) + ".sink.jsonl").read_text().splitlines()
        ]
        new_session = next(
            e["received"] for e in sink if e.get("received", {}).get("method") == "session/new"
        )
        headers = {
            item["name"]: item["value"]
            for item in new_session["params"]["mcpServers"][0]["headers"]
        }
        scoped = {"Authorization": headers["Authorization"]}
        assert client.post(f"{url_a}/resume", headers=scoped).status_code == 403
    finally:
        client.delete(url_a)


def test_resume_anonymous_returns_401(anon_client) -> None:
    response = anon_client.post("/api/workspaces/ws-1/studio-chat/sessions/s-1/resume")
    assert response.status_code == 401
