"""Shared skill material tools for the studio-agent MCP server (#633).

Split from ``skill_tools`` for the file-size budget, same registration
contract (``register_*`` onto the shared FastMCP instance). The tools are
workspace-scoped loopback tools and stay ``async def`` for the
single-event-loop reason documented in ``server.py``: they author the
workspace ``_shared`` materials and propagate them into each mapped
skill's repo (#673).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP

from server.app.mcp_server.config import McpServerConfig
from server.app.mcp_server.tool_client import (
    SYNC_PROPAGATE_TIMEOUT_SECONDS,
    ToolClient,
)

ClientFactory = Callable[[], Awaitable[tuple[McpServerConfig, ToolClient]]]


def register_shared_tools(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(structured_output=False)
    async def get_shared_materials(workspace_id: str) -> str:
        """Read the workspace's shared skill materials (_shared/map.json +
        references/ + scripts/ texts). No _shared → {"map": null, "files":
        []} — the signal to author them, not an error."""
        _, client = await client_factory()
        return await client.call("GET", f"/workspaces/{quote(workspace_id, safe='')}/skills-shared")

    @mcp.tool(structured_output=False)
    async def save_shared_materials(workspace_id: str, files: list[dict[str, str]]) -> str:
        """Author the workspace's shared materials under _shared/ (map.json +
        references/ + scripts/); map.json is just one of the files — you
        author its JSON. save_skill_version of a mapped skill syncs the
        shared sources into that skill's repo — never hand-supply a mapped
        path in the save payload (the shared copy wins; the save rejects
        it)."""
        _, client = await client_factory()
        body = {"files": files}
        return await client.call(
            "PUT", f"/workspaces/{quote(workspace_id, safe='')}/skills-shared", body
        )

    @mcp.tool(structured_output=False)
    async def sync_shared_materials(workspace_id: str, sources: list[str] | None = None) -> str:
        """Propagate _shared materials into each mapped skill repo: copy
        the shared sources in, commit and tag a new patch version per
        skill (omit sources for every mapped entry). Per-skill results
        (synced/skipped/failed + new tag); one failure never aborts the
        batch. Touches only local skill repos — the DB skill lock and
        node pins stay put."""
        _, client = await client_factory()
        return await client.call(
            "POST",
            f"/workspaces/{quote(workspace_id, safe='')}/skills-shared/propagate",
            {"sources": sources},
            timeout=SYNC_PROPAGATE_TIMEOUT_SECONDS,
        )
