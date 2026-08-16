"""Unit tests for the studio-agent MCP server (HTTP layer mocked).

Covers tool → endpoint forwarding (method/path/body), auth header assembly,
non-2xx passthrough as text, network-error degradation, and the fail-fast
config contract (missing scoped token refuses startup).
"""

from __future__ import annotations

import asyncio
import json

import pytest
import requests

from server.app.mcp_server.config import (
    API_BASE_ENV,
    DEFAULT_API_BASE,
    SESSION_ID_ENV,
    TOKEN_ENV,
    McpConfigError,
    McpServerConfig,
)
from server.app.mcp_server.server import create_mcp_server, main

pytestmark = pytest.mark.no_db

_CONFIG = McpServerConfig(api_base="http://backend.test:9000", token="scoped-token-1")


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: object = None, text: str = ""):
        self.status_code = status_code
        self.text = text or json.dumps(payload if payload is not None else {"ok": True})


def _run_tool(server, name: str, args: dict) -> str:
    blocks, _result = asyncio.run(server.call_tool(name, args))
    return "".join(block.text for block in blocks if block.type == "text")


@pytest.fixture
def recorded(monkeypatch):
    """Mock requests.request; returns (server, calls) with canned 200 JSON."""
    calls: list[dict] = []

    def fake_request(method, url, json=None, headers=None, timeout=None):  # noqa: A002
        calls.append(
            {"method": method, "url": url, "json": json, "headers": headers, "timeout": timeout}
        )
        return _FakeResponse(200, {"echo": True})

    monkeypatch.setattr("server.app.mcp_server.server.requests.request", fake_request)
    return create_mcp_server(_CONFIG), calls


def test_list_workflows_forwards_get(recorded) -> None:
    server, calls = recorded
    assert json.loads(_run_tool(server, "list_workflows", {})) == {"echo": True}
    assert calls == [
        {
            "method": "GET",
            "url": "http://backend.test:9000/api/studio-agent/tools/workflows",
            "json": None,
            "headers": {
                "Authorization": "Bearer scoped-token-1",
                "Content-Type": "application/json",
            },
            "timeout": 30,
        }
    ]


def test_get_active_workflow(recorded) -> None:
    server, calls = recorded
    _run_tool(server, "get_active_workflow", {"workspace_id": "ws-1"})
    assert calls[0]["url"].endswith("/workspaces/ws-1/workflow/active")
    assert calls[0]["method"] == "GET"


def test_validate_workflow_posts_definition(recorded) -> None:
    server, calls = recorded
    _run_tool(server, "validate_workflow", {"workspace_id": "ws-1", "definition_yaml": "key: x"})
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"].endswith("/workspaces/ws-1/workflow/validate")
    assert calls[0]["json"] == {"definition_yaml": "key: x"}


def test_compare_workflow_posts_definition(recorded) -> None:
    server, calls = recorded
    _run_tool(server, "compare_workflow", {"workspace_id": "ws-1", "definition_yaml": "k: v"})
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"].endswith("/workspaces/ws-1/workflow/compare")
    assert calls[0]["json"] == {"definition_yaml": "k: v"}


def test_save_node_code_draft_puts_code(recorded) -> None:
    server, calls = recorded
    args = {
        "workspace_id": "ws-1",
        "workflow_key": "wf",
        "node_key": "node",
        "code": "def run(job, job_dir, runtime):\n    return {}\n",
        "change_note": "note",
    }
    _run_tool(server, "save_node_code_draft", args)
    assert calls[0]["method"] == "PUT"
    assert calls[0]["url"].endswith("/workspaces/ws-1/workflows/wf/nodes/node/code/draft")
    assert calls[0]["json"] == {"code": args["code"], "change_note": "note"}


def test_save_node_code_draft_empty_note_becomes_null(recorded) -> None:
    server, calls = recorded
    args = {"workspace_id": "ws-1", "workflow_key": "wf", "node_key": "node", "code": "x"}
    _run_tool(server, "save_node_code_draft", args)
    assert calls[0]["json"]["change_note"] is None


def test_get_node_code(recorded) -> None:
    server, calls = recorded
    _run_tool(
        server, "get_node_code", {"workspace_id": "ws-1", "workflow_key": "wf", "node_key": "n"}
    )
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/workspaces/ws-1/workflows/wf/nodes/n/code")


def test_save_agent_definition_draft_default_tools(recorded) -> None:
    server, calls = recorded
    _run_tool(
        server,
        "save_agent_definition_draft",
        {"agent_id": "a-1", "capability": "cap", "runtime": "pi", "skill": "s/k"},
    )
    assert calls[0]["method"] == "PUT"
    assert calls[0]["url"].endswith("/agent-definitions/a-1/draft")
    assert calls[0]["json"] == {
        "capability": "cap",
        "runtime": "pi",
        "skill": "s/k",
        "tools": ["read", "write", "bash"],
    }


def test_register_workflow_posts_catalog_entry(recorded) -> None:
    server, calls = recorded
    _run_tool(server, "register_workflow", {"workflow_key": "wf-new", "label": "New"})
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"].endswith("/workflows/register")
    assert calls[0]["json"] == {"key": "wf-new", "label": "New", "description": ""}


def test_get_studio_context_uses_the_bound_session(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_request(method, url, json=None, headers=None, timeout=None):  # noqa: A002
        calls.append({"method": method, "url": url})
        return _FakeResponse(200, {"workspace_id": "ws-1"})

    monkeypatch.setattr("server.app.mcp_server.server.requests.request", fake_request)
    config = McpServerConfig(api_base="http://backend.test:9000", token="t", session_id="sess-1")
    server = create_mcp_server(config)
    assert json.loads(_run_tool(server, "get_studio_context", {})) == {"workspace_id": "ws-1"}
    assert calls == [
        {
            "method": "GET",
            "url": "http://backend.test:9000/api/studio-agent/tools/chat-sessions/sess-1/context",
        }
    ]


def test_get_studio_context_without_session_binding() -> None:
    # Self-service (external agent) setups carry no chat session binding.
    server = create_mcp_server(_CONFIG)
    text = _run_tool(server, "get_studio_context", {})
    assert "no chat session bound" in text


def test_non_2xx_returns_http_text(monkeypatch) -> None:
    def fake_request(method, url, json=None, headers=None, timeout=None):  # noqa: A002
        return _FakeResponse(403, text='{"detail":"Studio agent scoped token required"}')

    monkeypatch.setattr("server.app.mcp_server.server.requests.request", fake_request)
    server = create_mcp_server(_CONFIG)
    text = _run_tool(server, "list_workflows", {})
    assert text.startswith("HTTP 403: ")
    assert "scoped token" in text


def test_connection_error_returns_text_not_exception(monkeypatch) -> None:
    def fake_request(method, url, json=None, headers=None, timeout=None):  # noqa: A002
        raise requests.ConnectionError("refused")

    monkeypatch.setattr("server.app.mcp_server.server.requests.request", fake_request)
    server = create_mcp_server(_CONFIG)
    text = _run_tool(server, "list_workflows", {})
    assert text.startswith("request failed: ")
    assert "refused" in text


def test_config_requires_token() -> None:
    with pytest.raises(McpConfigError, match=TOKEN_ENV):
        McpServerConfig.from_env({})


def test_config_defaults_and_overrides() -> None:
    default = McpServerConfig.from_env({TOKEN_ENV: "  tok  "})
    assert default.api_base == DEFAULT_API_BASE
    assert default.token == "tok"
    assert default.session_id is None
    custom = McpServerConfig.from_env(
        {TOKEN_ENV: "tok", API_BASE_ENV: "http://example.test:8000/", SESSION_ID_ENV: " sess-9 "}
    )
    assert custom.api_base == "http://example.test:8000"
    assert custom.session_id == "sess-9"


def test_main_fails_fast_without_token(monkeypatch, capsys) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 2
    assert TOKEN_ENV in capsys.readouterr().err
