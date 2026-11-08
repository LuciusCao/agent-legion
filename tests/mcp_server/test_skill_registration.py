from __future__ import annotations

import asyncio
import inspect

import pytest
from mcp.server.fastmcp import FastMCP

from server.app.mcp_server.skill_tools import register_skill_tools

pytestmark = pytest.mark.no_db


def test_skill_registration_order_and_create_contract() -> None:
    mcp = FastMCP("skill-registration-test")
    calls: list[tuple[str, str, dict | None]] = []

    class Client:
        async def call(self, method: str, path: str, body: dict | None = None) -> str:
            calls.append((method, path, body))
            return '{"ok": true}'

    async def client_factory():
        return None, Client()

    register_skill_tools(mcp, client_factory)
    tools = mcp._tool_manager._tools

    assert list(tools) == ["get_skill", "validate_skill", "save_skill_version", "create_skill"]
    assert all(inspect.iscoroutinefunction(tool.fn) for tool in tools.values())
    create_skill = tools["create_skill"]
    assert create_skill.fn_metadata.output_model is None
    assert list(inspect.signature(create_skill.fn).parameters) == [
        "workspace_id",
        "skill_name",
        "files",
        "new_tag",
        "message",
    ]

    files = [{"path": "SKILL.md", "content": "# Skill"}]
    asyncio.run(
        mcp.call_tool(
            "create_skill",
            {
                "workspace_id": "ws-1",
                "skill_name": "review",
                "files": files,
                "new_tag": "v1",
                "message": "initial",
            },
        )
    )

    assert calls == [
        (
            "POST",
            "/workspaces/ws-1/skills",
            {
                "skill_name": "review",
                "files": files,
                "new_tag": "v1",
                "message": "initial",
            },
        )
    ]
