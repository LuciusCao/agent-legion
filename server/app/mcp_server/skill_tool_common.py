"""Shared client type and workspace-scoped URL construction for skill tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import quote

from server.app.mcp_server.config import McpServerConfig
from server.app.mcp_server.tool_client import ToolClient

ClientFactory = Callable[[], Awaitable[tuple[McpServerConfig, ToolClient]]]


def _skill_key_path(skill_key: str) -> str:
    """URL-encode each skill-key segment (keys are <group>/<name>)."""
    return "/".join(quote(segment, safe="") for segment in skill_key.split("/"))


def _skill_path(workspace_id: str, skill_key: str) -> str:
    """Workspace-scoped URL for a skill."""
    return f"/workspaces/{quote(workspace_id, safe='')}/skills/{_skill_key_path(skill_key)}"
