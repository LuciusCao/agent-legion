"""MCP 工具 → 真实路由表的契约测试（防止 stub 对 stub 漂移）。

tests/mcp_server/test_tools.py 用假 HTTP 层验证转发语义；这里把每个 MCP 工具
实际发出的 (method, path) 与工具面真实路由表（studio_agent_tools +
studio_agent_context 两个 router）做双向比对：工具参数一律填 "{参数名}" 占位，
记录到的 path 即路由模板本身，任何一边改名/改路径都会在这里炸出来。
"""

from __future__ import annotations

import asyncio
import re

import pytest

from server.app.mcp_server.config import McpServerConfig
from server.app.mcp_server.server import create_mcp_server
from server.app.routes.studio_agent_context import create_studio_agent_context_router
from server.app.routes.studio_agent_tools import create_studio_agent_tools_router
from server.app.settings import Settings

pytestmark = pytest.mark.no_db

_CONFIG = McpServerConfig(
    api_base="http://backend.test:9000", token="scoped-token-1", session_id="{session_id}"
)
_ROUTE_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}


class _FakeResponse:
    status_code = 200
    text = "{}"


def test_mcp_tools_match_the_real_tool_router(monkeypatch, tmp_path) -> None:
    calls: list[dict] = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, method, url, json=None, headers=None):  # noqa: A002
            calls.append({"method": method, "url": url})
            return _FakeResponse()

    monkeypatch.setattr("server.app.mcp_server.tool_client.httpx.AsyncClient", FakeAsyncClient)
    server = create_mcp_server(_CONFIG)

    tools = asyncio.run(server.list_tools())
    # 工具面清单变化时同步这里与工具文档（server/app/mcp_server/server.py）。
    # get_authoring_guide 是本地静态工具（不发 HTTP），不影响下方
    # recorded == table 的路由比对。
    assert len(tools) == 11
    for tool in tools:
        schema = tool.inputSchema
        args = {}
        for name, prop in (schema.get("properties") or {}).items():
            if name not in (schema.get("required") or []):
                continue
            # "{参数名}" 占位只用于字符串参数（让记录的 path 即路由模板）；
            # 非字符串参数给类型合法的空值（不影响 method+path 比对）。
            args[name] = f"{{{name}}}" if prop.get("type") in (None, "string") else []
        asyncio.run(server.call_tool(tool.name, args))

    settings = Settings(
        root_dir=tmp_path,
        data_dir=tmp_path,
        videos_dir=tmp_path,
        logs_dir=tmp_path,
        packages_dir=tmp_path,
        jobs_dir=tmp_path,
        config={},
    )
    router = create_studio_agent_tools_router(None, settings)  # 枚举路由不触 DB
    table = {
        # `{param:path}` 转换器归一化为 `{param}`：工具侧占位不含转换器后缀。
        (method, re.sub(r"\{(\w+):\w+\}", r"{\1}", route.path))
        for router_ in (router, create_studio_agent_context_router(None))
        for route in router_.routes
        for method in (route.methods or set()) & _ROUTE_METHODS
    }

    recorded = {
        (call["method"], call["url"].removeprefix(_CONFIG.api_base).removeprefix("/api"))
        for call in calls
    }
    assert recorded == table
