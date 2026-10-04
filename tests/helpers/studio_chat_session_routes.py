"""Shared route-test helpers for Studio chat session management (#872
rename / delete, #924 archive): fake ACP agent registration, workspace and
session seeding, scoped-token extraction and member clients. Test modules
must not import each other (test_pytest_postgres_boundaries), so the route
suites share these through tests/helpers."""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

FAKE_AGENT = Path(__file__).resolve().parent / "fake_acp_agent.py"

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

PERMISSION_SCRIPT = {
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

CREATED: list[tuple[str, str]] = []


def close_created_sessions(client) -> None:
    """Backstop teardown: a mid-test failure must not orphan fake ACP
    subprocesses (callers wrap it in an autouse fixture)."""
    for workspace_id, session_id in CREATED:
        with contextlib.suppress(Exception):
            client.delete(url(workspace_id, session_id))
    CREATED.clear()


def register_fake_agent(client, tmp_path, script: dict | None = None) -> Path:
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


def create_workspace(client, suffix: str = "") -> str:
    response = client.post(
        "/api/workspaces",
        json={"id": f"chat_manage_ws{suffix}", "name": f"Chat Manage{suffix}"},
    )
    assert response.status_code == 200, response.text
    return response.json()["workspace"]["id"]


def create_session(client, workspace_id: str, title: str = "t") -> str:
    response = client.post(
        f"/api/workspaces/{workspace_id}/studio-chat/sessions",
        json={"agent_id": "fake-agent", "title": title},
    )
    assert response.status_code == 200, response.text
    session_id = response.json()["session"]["id"]
    CREATED.append((workspace_id, session_id))
    return session_id


def url(workspace_id: str, session_id: str) -> str:
    return f"/api/workspaces/{workspace_id}/studio-chat/sessions/{session_id}"


def list_ids(client, workspace_id: str) -> list[str]:
    response = client.get(f"/api/workspaces/{workspace_id}/studio-chat/sessions")
    assert response.status_code == 200, response.text
    return [row["id"] for row in response.json()["sessions"]]


def scoped_headers(script_path: Path) -> dict[str, str]:
    sink = [
        json.loads(line) for line in Path(str(script_path) + ".sink.jsonl").read_text().splitlines()
    ]
    # Latest session/new = the most recently created session's token (the
    # fake agent script, hence the sink, is shared by every session).
    new_session = [
        e["received"] for e in sink if e.get("received", {}).get("method") == "session/new"
    ][-1]
    headers = {
        item["name"]: item["value"] for item in new_session["params"]["mcpServers"][0]["headers"]
    }
    return {"Authorization": headers["Authorization"]}


def member_client(client, username: str):
    response = client.post("/api/users", json={"username": username, "password": "pw1"})
    assert response.status_code == 201, response.text
    member_id = response.json()["id"]
    member = client.__class__(client.app)
    response = member.post("/api/auth/login", json={"username": username, "password": "pw1"})
    assert response.status_code == 200, response.text
    member.headers["x-agent-legion-request"] = "1"
    return member, member_id
