"""Tests for the in-app streamable-HTTP MCP endpoint (/api/studio-agent/mcp).

kimi >= 0.38 only accepts http/sse MCP servers in ACP session/new, so Studio
chat sessions point at this endpoint instead of a stdio subprocess. The ASGI
auth wrapper is the only guard on the mount (STUDIO-AGENT-001): no valid
studio-agent scoped Bearer token, no MCP handshake. Tool calls keep looping
back through the /api/studio-agent/tools/* route layer.
"""

from __future__ import annotations

import json

from server.app.auth.scoped_tokens import mint_scoped_token, revoke_scoped_token
from server.app.mcp_server.config import SESSION_ID_HEADER
from server.app.mcp_server.tool_client import ToolClient

MCP_URL = "/api/studio-agent/mcp"

_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}

_ACCEPT = {"Accept": "application/json, text/event-stream"}
# The MCP transport validates the Host header against a localhost allowlist
# (DNS-rebinding protection); TestClient's default "testserver" host is 421'd.
_HOST = {"Host": "127.0.0.1:8000"}


def _mint(job_db, scope: str = "studio_agent") -> str:
    user_id = str(job_db.create_user(f"mcp-http-{scope}", password_hash=None)["id"])
    return mint_scoped_token(job_db, user_id, scope=scope, origin="run")


def _post(client, token: str, payload: dict, session_id: str | None = None, mcp_session=None):
    headers = {"Authorization": f"Bearer {token}", **_ACCEPT, **_HOST}
    if session_id is not None:
        headers[SESSION_ID_HEADER] = session_id
    if mcp_session is not None:
        headers["Mcp-Session-Id"] = mcp_session
    return client.post(MCP_URL, json=payload, headers=headers)


def _sse_data(response) -> dict:
    for line in response.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    raise AssertionError(f"no SSE data frame in response: {response.text!r}")


def _open_session(client, token: str) -> str:
    response = _post(client, token, _INITIALIZE)
    assert response.status_code == 200, response.text
    result = _sse_data(response)["result"]
    assert result["serverInfo"]["name"] == "agent-legion-studio"
    mcp_session = response.headers["mcp-session-id"]
    notified = _post(
        client,
        token,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        mcp_session=mcp_session,
    )
    assert notified.status_code == 202, notified.text
    return mcp_session


def test_mcp_endpoint_rejects_missing_token(anon_client) -> None:
    response = anon_client.post(MCP_URL, json=_INITIALIZE, headers={**_ACCEPT, **_HOST})
    assert response.status_code == 401


def test_mcp_endpoint_rejects_non_studio_scope(client, job_db) -> None:
    token = _mint(job_db, scope="other")
    response = _post(client, token, _INITIALIZE)
    assert response.status_code == 401


def test_mcp_endpoint_rejects_revoked_token(client, job_db) -> None:
    token = _mint(job_db)
    revoke_scoped_token(job_db, token)
    response = _post(client, token, _INITIALIZE)
    assert response.status_code == 401


def test_initialize_and_tool_listing(client, job_db) -> None:
    token = _mint(job_db)
    mcp_session = _open_session(client, token)

    response = _post(
        client,
        token,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        mcp_session=mcp_session,
    )
    assert response.status_code == 200, response.text
    names = sorted(tool["name"] for tool in _sse_data(response)["result"]["tools"])
    assert names == [
        "compare_workflow",
        "get_active_workflow",
        "get_authoring_guide",
        "get_node_code",
        "get_studio_context",
        "save_agent_definition_draft",
        "save_node_code_draft",
        "validate_workflow",
    ]


def test_tool_call_forwards_token_and_session_binding(client, job_db, monkeypatch) -> None:
    token = _mint(job_db)
    mcp_session = _open_session(client, token)

    captured: dict = {}

    async def fake_call(self, method: str, path: str, body=None) -> str:
        captured["authorization"] = self._headers["Authorization"]
        captured["method"] = method
        captured["path"] = path
        return "ok"

    monkeypatch.setattr(ToolClient, "call", fake_call)
    response = _post(
        client,
        token,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "get_studio_context", "arguments": {}},
        },
        session_id="sess-xyz",
        mcp_session=mcp_session,
    )
    assert response.status_code == 200, response.text
    content = _sse_data(response)["result"]["content"]
    assert content[0]["text"] == "ok"
    assert captured["authorization"] == f"Bearer {token}"
    assert captured["path"].endswith("/chat-sessions/sess-xyz/context")
