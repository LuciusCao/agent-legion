"""Session context endpoint for the get_studio_context MCP tool (schema v45).

``GET /api/studio-agent/tools/chat-sessions/{session_id}/context`` returns the
session's bound workspace, the human's live Studio node selection, and the
active workflow's structural summary. A workspace-bound run token may only
read sessions of its own workspace; an unbound self-service token must belong
to the session's workspace (admins pass). Mismatches are 404 so session ids
of other workspaces cannot be probed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from server.app.auth import scoped_tokens

FAKE_AGENT = Path(__file__).resolve().parents[1] / "helpers" / "fake_acp_agent.py"


def _register_fake_agent(client, tmp_path) -> Path:
    script_path = tmp_path / "fake-agent-script.json"
    script_path.write_text(json.dumps({"on_prompt": []}), encoding="utf-8")
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


def _create_workspace(client, name: str = "Context WS") -> str:
    response = client.post(
        "/api/workspaces",
        json={"name": name, "default_workflow_key": "education_video_problems_generation"},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["workspace"]["id"])


def _create_session(client, workspace_id: str) -> str:
    response = client.post(
        f"/api/workspaces/{workspace_id}/studio-chat/sessions",
        json={"agent_id": "fake-agent", "title": ""},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["session"]["id"])


def _session_token(script_path: Path) -> str:
    sink = [
        json.loads(line) for line in Path(str(script_path) + ".sink.jsonl").read_text().splitlines()
    ]
    new_session = next(
        e["received"] for e in sink if e.get("received", {}).get("method") == "session/new"
    )
    env = {item["name"]: item["value"] for item in new_session["params"]["mcpServers"][0]["env"]}
    return str(env["AGENT_LEGION_STUDIO_AGENT_TOKEN"])


def _context_url(session_id: str) -> str:
    return f"/api/studio-agent/tools/chat-sessions/{session_id}/context"


def test_bound_token_reads_own_session_context(client, job_db, tmp_path) -> None:
    script_path = _register_fake_agent(client, tmp_path)
    workspace_id = _create_workspace(client)
    session_id = _create_session(client, workspace_id)
    token = _session_token(script_path)
    try:
        selected = client.put(
            f"/api/workspaces/{workspace_id}/studio-chat/sessions/{session_id}/context",
            json={"selected_node_key": "parse_question"},
        )
        assert selected.status_code == 200, selected.text

        response = client.get(
            _context_url(session_id), headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200, response.text
        context = response.json()
        assert context["workspace_id"] == workspace_id
        assert context["selected_node_key"] == "parse_question"
        workflow = context["workflow"]
        assert workflow["workflow_key"] == "education_video_problems_generation"
        assert workflow["version"] >= 1
        assert workflow["nodes"] and {node["capability"] for node in workflow["nodes"]}
        assert all(set(node) == {"key", "capability"} for node in workflow["nodes"])
    finally:
        client.delete(f"/api/workspaces/{workspace_id}/studio-chat/sessions/{session_id}")


def test_bound_token_gets_404_on_foreign_workspace_session(client, job_db, tmp_path) -> None:
    script_path = _register_fake_agent(client, tmp_path)
    workspace_a = _create_workspace(client, "Context WS A")
    workspace_b = _create_workspace(client, "Context WS B")
    session_a = _create_session(client, workspace_a)
    session_b = _create_session(client, workspace_b)
    token_a = _session_token(script_path)
    try:
        foreign = client.get(
            _context_url(session_b), headers={"Authorization": f"Bearer {token_a}"}
        )
        assert foreign.status_code == 404
        own = client.get(_context_url(session_a), headers={"Authorization": f"Bearer {token_a}"})
        assert own.status_code == 200, own.text
        assert own.json()["workspace_id"] == workspace_a
    finally:
        client.delete(f"/api/workspaces/{workspace_a}/studio-chat/sessions/{session_a}")
        client.delete(f"/api/workspaces/{workspace_b}/studio-chat/sessions/{session_b}")


def test_unbound_self_service_token_keeps_legacy_behaviour(client, job_db, tmp_path) -> None:
    """Tokens minted via /api/studio-agent-tokens carry no workspace binding;
    held by an admin they read any session's context (membership rule for
    non-admin holders is covered by the next test)."""
    _register_fake_agent(client, tmp_path)
    workspace_id = _create_workspace(client)
    session_id = _create_session(client, workspace_id)
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id, origin="user")
    try:
        response = client.get(
            _context_url(session_id), headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["workspace_id"] == workspace_id
        assert response.json()["selected_node_key"] is None
    finally:
        client.delete(f"/api/workspaces/{workspace_id}/studio-chat/sessions/{session_id}")


def test_unbound_token_requires_workspace_membership(client, job_db, tmp_path) -> None:
    """P0-1: an unbound self-service token is not a free pass — a non-member
    holder gets 404 (session ids cannot be probed), a workspace member (even
    a viewer) reads the context."""
    _register_fake_agent(client, tmp_path)
    workspace_id = _create_workspace(client)
    session_id = _create_session(client, workspace_id)
    outsider_id = str(job_db.create_user("ctx-outsider", password_hash=None)["id"])
    outsider = client.__class__(client.app)
    outsider.headers["authorization"] = (
        f"Bearer {scoped_tokens.mint_scoped_token(job_db, outsider_id, origin='user')}"
    )
    try:
        assert outsider.get(_context_url(session_id)).status_code == 404
        job_db.upsert_workspace_member(workspace_id, outsider_id, "viewer")
        response = outsider.get(_context_url(session_id))
        assert response.status_code == 200, response.text
        assert response.json()["workspace_id"] == workspace_id
    finally:
        client.delete(f"/api/workspaces/{workspace_id}/studio-chat/sessions/{session_id}")


def test_unknown_session_is_404_and_full_session_is_403(client, job_db, tmp_path) -> None:
    del tmp_path
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id, origin="user")
    scoped = client.__class__(client.app)
    scoped.headers["authorization"] = f"Bearer {token}"
    assert scoped.get(_context_url("no-such-session")).status_code == 404
    # Full user sessions never reach the tool surface; anonymous gets 401.
    assert client.get(_context_url("no-such-session")).status_code == 403
    anon = client.__class__(client.app)
    assert anon.get(_context_url("no-such-session")).status_code == 401
