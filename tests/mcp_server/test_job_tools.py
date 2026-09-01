"""Unit tests for the job observation MCP tools (issue #329).

Self-contained by design: the tools are registered on a FRESH FastMCP
instance here (the shared ``server.py`` registration line and the shared
test files land with the parallel mission that owns them). Covers
tool → endpoint forwarding (method/path/query), the session-binding
degradation of ``get_job_context``, and the async constraint (the in-app
HTTP transport executes tools on the uvicorn event loop — sync loopback
tools deadlock it, see server.py).
"""

from __future__ import annotations

import asyncio
import inspect
import json

import pytest
from mcp.server.fastmcp import FastMCP

from server.app.mcp_server.config import McpServerConfig
from server.app.mcp_server.job_tools import register_job_tools
from server.app.mcp_server.tool_client import ToolClient

pytestmark = pytest.mark.no_db

_CONFIG = McpServerConfig(
    api_base="http://backend.test:9000", token="scoped-token-1", session_id="sess-1"
)


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: object = None):
        self.status_code = status_code
        self.text = json.dumps(payload if payload is not None else {"ok": True})


def _build_server(monkeypatch, calls: list[dict], config: McpServerConfig = _CONFIG) -> FastMCP:
    """Fresh FastMCP + job tools, with the httpx loopback mocked inline."""

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, method, url, json=None, headers=None):  # noqa: A002
            calls.append({"method": method, "url": url, "json": json})
            return _FakeResponse(200, {"echo": True})

    monkeypatch.setattr("server.app.mcp_server.tool_client.httpx.AsyncClient", FakeAsyncClient)

    async def client_factory() -> tuple[McpServerConfig, ToolClient]:
        return config, ToolClient(config)

    mcp = FastMCP("test-job-tools")
    register_job_tools(mcp, client_factory)
    return mcp


def _run_tool(server: FastMCP, name: str, args: dict) -> str:
    blocks, _result = asyncio.run(server.call_tool(name, args))
    return "".join(block.text for block in blocks if block.type == "text")


def test_registers_six_read_only_async_tools(monkeypatch) -> None:
    server = _build_server(monkeypatch, [])
    tools = server._tool_manager._tools  # pinned mcp==1.29 internals
    expected = {
        "get_job_context",
        "get_job_detail",
        "get_node_logs",
        "read_artifact",
        "list_jobs",
        "compare_jobs",
    }
    assert expected <= set(tools)
    for name in expected:
        assert inspect.iscoroutinefunction(tools[name].fn), name


def test_get_job_context_uses_bound_session(monkeypatch) -> None:
    calls: list[dict] = []
    server = _build_server(monkeypatch, calls)

    _run_tool(server, "get_job_context", {"job_id": "job 1", "node_key": "写脚本"})

    assert calls[0]["method"] == "GET"
    assert calls[0]["url"] == (
        "http://backend.test:9000/api/studio-agent/tools/chat-sessions/sess-1"
        "/job-context?job_id=job+1&node_key=%E5%86%99%E8%84%9A%E6%9C%AC"
    )


def test_get_job_context_omits_empty_node_key(monkeypatch) -> None:
    calls: list[dict] = []
    server = _build_server(monkeypatch, calls)

    _run_tool(server, "get_job_context", {"job_id": "job-1"})

    assert calls[0]["url"].endswith("/job-context?job_id=job-1")


def test_get_job_context_without_session_binding(monkeypatch) -> None:
    # Self-service (external agent) setups carry no chat session binding.
    calls: list[dict] = []
    config = McpServerConfig(api_base="http://backend.test:9000", token="t")
    server = _build_server(monkeypatch, calls, config)

    text = _run_tool(server, "get_job_context", {"job_id": "job-1"})

    assert "no chat session bound" in text
    assert calls == []


def test_get_job_detail_forwards_path(monkeypatch) -> None:
    calls: list[dict] = []
    server = _build_server(monkeypatch, calls)

    _run_tool(server, "get_job_detail", {"workspace_id": "ws 1", "job_id": "job-1"})

    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/workspaces/ws%201/jobs/job-1")


def test_get_node_logs_forwards_selectors(monkeypatch) -> None:
    calls: list[dict] = []
    server = _build_server(monkeypatch, calls)

    _run_tool(server, "get_node_logs", {"workspace_id": "ws-1", "job_id": "job-1"})
    assert calls[0]["url"].endswith("/workspaces/ws-1/jobs/job-1/logs")

    _run_tool(
        server,
        "get_node_logs",
        {"workspace_id": "ws-1", "job_id": "job-1", "node_key": "gen", "run_id": 7},
    )
    assert calls[1]["url"].endswith("/workspaces/ws-1/jobs/job-1/logs?node_key=gen&run_id=7")


def test_read_artifact_url_encodes_name(monkeypatch) -> None:
    calls: list[dict] = []
    server = _build_server(monkeypatch, calls)

    _run_tool(
        server,
        "read_artifact",
        {"workspace_id": "ws-1", "job_id": "job-1", "artifact_name": "my report.json"},
    )

    assert calls[0]["url"].endswith("/workspaces/ws-1/jobs/job-1/artifacts/my%20report.json")


def test_list_jobs_forwards_filters(monkeypatch) -> None:
    calls: list[dict] = []
    server = _build_server(monkeypatch, calls)

    _run_tool(server, "list_jobs", {"workspace_id": "ws-1"})
    assert calls[0]["url"].endswith("/workspaces/ws-1/jobs?limit=20")

    _run_tool(server, "list_jobs", {"workspace_id": "ws-1", "status": "failed", "limit": 5})
    assert calls[1]["url"].endswith("/workspaces/ws-1/jobs?status=failed&limit=5")


def test_compare_jobs_forwards_pair(monkeypatch) -> None:
    calls: list[dict] = []
    server = _build_server(monkeypatch, calls)

    _run_tool(server, "compare_jobs", {"workspace_id": "ws-1", "job_id_a": "a", "job_id_b": "b"})

    assert calls[0]["url"].endswith("/workspaces/ws-1/jobs/compare?job_id_a=a&job_id_b=b")
