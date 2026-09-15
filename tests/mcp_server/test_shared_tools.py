"""Unit tests for the shared-material MCP tools (#633).

Self-contained like test_job_tools.py: the tools are registered on a
fresh FastMCP instance with the httpx loopback mocked inline. Covers
tool → endpoint forwarding (method/path/body, workspace-id URL encoding)
and the async constraint (the in-app HTTP transport executes tools on the
uvicorn event loop — sync loopback tools deadlock it, see server.py).
"""

from __future__ import annotations

import asyncio
import inspect
import json

import pytest
from mcp.server.fastmcp import FastMCP

from server.app.mcp_server.config import McpServerConfig
from server.app.mcp_server.shared_tools import register_shared_tools
from server.app.mcp_server.tool_client import ToolClient

pytestmark = pytest.mark.no_db

_CONFIG = McpServerConfig(api_base="http://backend.test:9000", token="scoped-token-1")


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: object = None):
        self.status_code = status_code
        self.text = json.dumps(payload if payload is not None else {"ok": True})


def _build_server(monkeypatch, calls: list[dict], inits: list[dict] | None = None) -> FastMCP:
    class FakeAsyncClient:
        def __init__(self, **kwargs):
            if inits is not None:
                inits.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, method, url, json=None, headers=None):  # noqa: A002
            calls.append({"method": method, "url": url, "json": json})
            return _FakeResponse(200, {"echo": True})

    monkeypatch.setattr("server.app.mcp_server.tool_client.httpx.AsyncClient", FakeAsyncClient)

    async def client_factory() -> tuple[McpServerConfig, ToolClient]:
        return _CONFIG, ToolClient(_CONFIG)

    mcp = FastMCP("test-shared-tools")
    register_shared_tools(mcp, client_factory)
    return mcp


def _run(server: FastMCP, name: str, args: dict) -> str:
    # structured_output=False (#660): call_tool returns bare content blocks.
    blocks = asyncio.run(server.call_tool(name, args))
    return "".join(block.text for block in blocks if block.type == "text")


def test_registers_three_async_tools(monkeypatch) -> None:
    server = _build_server(monkeypatch, [])
    tools = server._tool_manager._tools  # pinned mcp==1.29 internals
    assert sorted(tools) == [
        "get_shared_materials",
        "save_shared_materials",
        "sync_shared_materials",
    ]
    for name in ("get_shared_materials", "save_shared_materials", "sync_shared_materials"):
        assert inspect.iscoroutinefunction(tools[name].fn), name


def test_get_shared_materials_forwards_get(monkeypatch) -> None:
    calls: list[dict] = []
    server = _build_server(monkeypatch, calls)
    text = _run(server, "get_shared_materials", {"workspace_id": "ws-1"})
    assert json.loads(text) == {"echo": True}
    assert calls[0]["method"] == "GET"
    assert (
        calls[0]["url"]
        == "http://backend.test:9000/api/studio-agent/tools/workspaces/ws-1/skills-shared"
    )


def test_save_shared_materials_forwards_put_body(monkeypatch) -> None:
    calls: list[dict] = []
    server = _build_server(monkeypatch, calls)
    files = [
        {"path": "map.json", "content": '{"version": 1, "materials": []}'},
        {"path": "references/style.md", "content": "# style\n"},
    ]
    _run(server, "save_shared_materials", {"workspace_id": "ws-1", "files": files})
    assert calls[0]["method"] == "PUT"
    assert (
        calls[0]["url"]
        == "http://backend.test:9000/api/studio-agent/tools/workspaces/ws-1/skills-shared"
    )
    assert calls[0]["json"] == {"files": files}


def test_shared_tools_url_encode_workspace_id(monkeypatch) -> None:
    calls: list[dict] = []
    server = _build_server(monkeypatch, calls)
    _run(server, "get_shared_materials", {"workspace_id": "ws 1"})
    assert calls[0]["url"].endswith("/workspaces/ws%201/skills-shared")


def test_sync_shared_materials_forwards_post(monkeypatch) -> None:
    calls: list[dict] = []
    server = _build_server(monkeypatch, calls)
    _run(
        server,
        "sync_shared_materials",
        {"workspace_id": "ws-1", "sources": ["references/style.md"]},
    )
    assert calls[0]["method"] == "POST"
    assert (
        calls[0]["url"]
        == "http://backend.test:9000/api/studio-agent/tools/workspaces/ws-1/skills-shared/propagate"
    )
    assert calls[0]["json"] == {"sources": ["references/style.md"]}


def test_sync_shared_materials_omitted_sources_forwards_null(monkeypatch) -> None:
    calls: list[dict] = []
    server = _build_server(monkeypatch, calls)
    _run(server, "sync_shared_materials", {"workspace_id": "ws-1"})
    assert calls[0]["json"] == {"sources": None}


def test_sync_shared_materials_uses_extended_timeout(monkeypatch) -> None:
    """codex P1（#674 收尾）：传播批次是逐 skill git 保存 + 锁等待，
    固定 30s 会让 MCP 端报 request failed 而后端继续写——传播调用用
    专用 300s 覆盖（依据注释在 tool_client.SYNC_PROPAGATE_TIMEOUT_SECONDS）。"""
    calls: list[dict] = []
    inits: list[dict] = []
    server = _build_server(monkeypatch, calls, inits)
    _run(server, "sync_shared_materials", {"workspace_id": "ws-1"})
    assert inits[0]["timeout"] == 300


def test_default_timeout_stays_30s_for_other_tools(monkeypatch) -> None:
    calls: list[dict] = []
    inits: list[dict] = []
    server = _build_server(monkeypatch, calls, inits)
    _run(server, "get_shared_materials", {"workspace_id": "ws-1"})
    assert inits[0]["timeout"] == 30
