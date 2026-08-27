"""Unit tests for the studio-agent MCP server (HTTP layer mocked).

Covers tool → endpoint forwarding (method/path/body), auth header assembly,
non-2xx passthrough as text, network-error degradation, and the fail-fast
config contract (missing scoped token refuses startup).
"""

from __future__ import annotations

import asyncio
import inspect
import json

import httpx
import pytest

from server.app.mcp_server.config import (
    API_BASE_ENV,
    DEFAULT_API_BASE,
    SESSION_ID_ENV,
    TOKEN_ENV,
    McpConfigError,
    McpServerConfig,
)
from server.app.mcp_server.server import create_mcp_server, main
from server.app.mcp_server.tool_client import ToolClient

pytestmark = pytest.mark.no_db

_CONFIG = McpServerConfig(api_base="http://backend.test:9000", token="scoped-token-1")


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: object = None, text: str = ""):
        self.status_code = status_code
        self.text = text or json.dumps(payload if payload is not None else {"ok": True})


def _patch_http_client(monkeypatch, handler) -> None:
    """Patch httpx.AsyncClient so ToolClient.call runs ``handler`` inline."""

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self._timeout = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, method, url, json=None, headers=None):  # noqa: A002
            return handler(method, url, json=json, headers=headers, timeout=self._timeout)

    monkeypatch.setattr("server.app.mcp_server.tool_client.httpx.AsyncClient", FakeAsyncClient)


def _run_tool(server, name: str, args: dict) -> str:
    blocks, _result = asyncio.run(server.call_tool(name, args))
    return "".join(block.text for block in blocks if block.type == "text")


@pytest.fixture
def recorded(monkeypatch):
    """Mock the httpx loopback; returns (server, calls) with canned 200 JSON."""
    calls: list[dict] = []

    def fake_request(method, url, json=None, headers=None, timeout=None):  # noqa: A002
        calls.append(
            {"method": method, "url": url, "json": json, "headers": headers, "timeout": timeout}
        )
        return _FakeResponse(200, {"echo": True})

    _patch_http_client(monkeypatch, fake_request)
    return create_mcp_server(_CONFIG), calls


def test_get_authoring_guide_is_served_locally(recorded) -> None:
    server, calls = recorded
    text = _run_tool(server, "get_authoring_guide", {})
    # The playbook ships with the MCP server: no HTTP call to the backend.
    assert calls == []
    assert text.startswith("# Agent Legion Workflow Authoring Guide")
    # Guard the sections the from-scratch flow depends on.
    for section in (
        "Tool map",
        "From-scratch flow",
        "Workflow definition YAML",
        "Capabilities and node kinds",
        "Agent definitions and tunables",
        "Common errors",
    ):
        assert section in text


def test_loopback_tools_are_async() -> None:
    # The in-app HTTP transport executes tools inline on the uvicorn event
    # loop (FastMCP runs sync tools without to_thread), so a sync loopback
    # tool deadlocks the single-worker backend against its own request —
    # the prod symptom was every tool call hanging to the 30s read timeout.
    server = create_mcp_server(_CONFIG)
    tools = server._tool_manager._tools  # pinned mcp==1.29 internals
    for name in (
        "get_studio_context",
        "get_active_workflow",
        "validate_workflow",
        "compare_workflow",
        "save_node_code_draft",
        "get_node_code",
        "save_agent_definition_draft",
        "get_skill",
        "validate_skill",
        "save_skill_version",
    ):
        assert inspect.iscoroutinefunction(tools[name].fn), name
    # The local-only playbook tool never blocks, so it stays sync.
    assert not inspect.iscoroutinefunction(tools["get_authoring_guide"].fn)


def test_loopback_call_is_fully_async(monkeypatch) -> None:
    # True async I/O, no thread offload: the loopback target's sync handlers
    # run on the same shared anyio worker pool, so anyio.to_thread would let
    # concurrent tool calls occupy every worker while the handler they wait
    # on can never start (thread-pool deadlock under concurrency).
    assert inspect.iscoroutinefunction(ToolClient.call)

    def fake_request(method, url, json=None, headers=None, timeout=None):  # noqa: A002
        return _FakeResponse(200, {"echo": True})

    _patch_http_client(monkeypatch, fake_request)
    client = ToolClient(_CONFIG)
    text = asyncio.run(client.call("GET", "/workspaces/ws-1/workflow/active"))
    assert json.loads(text) == {"echo": True}


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
    assert "expected_capability" not in calls[0]["json"]


def test_save_node_code_draft_forwards_expected_capability(recorded) -> None:
    server, calls = recorded
    args = {
        "workspace_id": "ws-1",
        "workflow_key": "wf",
        "node_key": "node",
        "code": "def run(job, job_dir, runtime):\n    return {}\n",
        "expected_capability": "publish_content",
    }
    _run_tool(server, "save_node_code_draft", args)
    assert calls[0]["json"]["expected_capability"] == "publish_content"


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
        {
            "workspace_id": "ws-1",
            "agent_id": "a-1",
            "capability": "cap",
            "runtime": "pi",
            "skill": "s/k",
        },
    )
    assert calls[0]["method"] == "PUT"
    assert calls[0]["url"].endswith("/workspaces/ws-1/agent-definitions/a-1/draft")
    assert calls[0]["json"] == {
        "capability": "cap",
        "runtime": "pi",
        "skill": "s/k",
        "tools": ["read", "write", "bash"],
    }


def test_get_skill_without_ref(recorded) -> None:
    server, calls = recorded
    _run_tool(server, "get_skill", {"skill_key": "wf/review"})
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/skills/wf/review")


def test_get_skill_with_ref_appends_query(recorded) -> None:
    server, calls = recorded
    _run_tool(server, "get_skill", {"skill_key": "wf/review", "ref": "v1.2.0+exp"})
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/skills/wf/review?ref=v1.2.0%2Bexp")


def test_skill_tools_url_encode_skill_key_segments(recorded) -> None:
    server, calls = recorded
    _run_tool(server, "get_skill", {"skill_key": "wf/re view"})
    assert "/skills/wf/re%20view" in calls[0]["url"]
    _run_tool(server, "validate_skill", {"skill_key": "wf/re view"})
    assert "/skills/wf/re%20view/validate" in calls[1]["url"]
    _run_tool(
        server,
        "save_skill_version",
        {
            "skill_key": "wf/re view",
            "files": [{"path": "SKILL.md", "content": "x"}],
            "new_tag": "v2",
            "message": "m",
        },
    )
    assert "/skills/wf/re%20view/versions" in calls[2]["url"]


def test_validate_skill_posts(recorded) -> None:
    server, calls = recorded
    _run_tool(server, "validate_skill", {"skill_key": "wf/review"})
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"].endswith("/skills/wf/review/validate")


def test_save_skill_version_posts_body(recorded) -> None:
    server, calls = recorded
    files = [{"path": "SKILL.md", "content": "# v2\n"}]
    _run_tool(
        server,
        "save_skill_version",
        {"skill_key": "wf/review", "files": files, "new_tag": "v2.0.0", "message": "revise"},
    )
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"].endswith("/skills/wf/review/versions")
    assert calls[0]["json"] == {"files": files, "new_tag": "v2.0.0", "message": "revise"}


def test_get_studio_context_uses_the_bound_session(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_request(method, url, json=None, headers=None, timeout=None):  # noqa: A002
        calls.append({"method": method, "url": url})
        return _FakeResponse(200, {"workspace_id": "ws-1"})

    _patch_http_client(monkeypatch, fake_request)
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

    _patch_http_client(monkeypatch, fake_request)
    server = create_mcp_server(_CONFIG)
    text = _run_tool(server, "get_active_workflow", {"workspace_id": "ws-1"})
    assert text.startswith("HTTP 403: ")
    assert "scoped token" in text


def test_connection_error_returns_text_not_exception(monkeypatch) -> None:
    def fake_request(method, url, json=None, headers=None, timeout=None):  # noqa: A002
        raise httpx.ConnectError("refused")

    _patch_http_client(monkeypatch, fake_request)
    server = create_mcp_server(_CONFIG)
    text = _run_tool(server, "get_active_workflow", {"workspace_id": "ws-1"})
    assert text.startswith("request failed: ")
    assert "refused" in text


def test_loopback_client_ignores_env_proxies(monkeypatch) -> None:
    """A socks ALL_PROXY without socksio must not break the loopback client."""
    captured: dict = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, method, url, json=None, headers=None):  # noqa: A002
            return _FakeResponse(200)

    monkeypatch.setattr("server.app.mcp_server.tool_client.httpx.AsyncClient", FakeAsyncClient)
    asyncio.run(ToolClient(_CONFIG).call("GET", "/workspaces/ws-1/workflow"))
    assert captured.get("trust_env") is False


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
