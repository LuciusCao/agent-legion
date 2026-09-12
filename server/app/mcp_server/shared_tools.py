"""Shared skill material tools for the studio-agent MCP server (#633).

Split from ``skill_tools`` for the file-size budget, same registration
contract (``register_*`` onto the shared FastMCP instance). Both tools are
workspace-scoped loopback tools and stay ``async def`` for the
single-event-loop reason documented in ``server.py``: they author the
workspace ``_shared`` materials that ``save_skill_version`` syncs into
each mapped skill's repo.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP

from server.app.mcp_server.config import McpServerConfig
from server.app.mcp_server.tool_client import ToolClient

ClientFactory = Callable[[], Awaitable[tuple[McpServerConfig, ToolClient]]]


def register_shared_tools(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool()
    async def get_shared_materials(workspace_id: str) -> str:
        """Read a workspace's shared skill materials: the parsed
        _shared/map.json (materials -> skills mapping) plus the readable
        text files under _shared/references and _shared/scripts. A
        workspace without _shared returns the structured empty state
        {"map": null, "files": []} — that is the signal to author them
        with save_shared_materials, not an error."""
        _, client = await client_factory()
        return await client.call("GET", f"/workspaces/{quote(workspace_id, safe='')}/skills-shared")

    @mcp.tool()
    async def save_shared_materials(workspace_id: str, files: list[dict[str, str]]) -> str:
        """Author a workspace's shared skill materials under _shared/
        (map.json + references/ + scripts/). map.json is just one of the
        files — you author its JSON: {"version": 1, "materials":
        [{"source": "references/style.md", "skills": ["skill-a"]}]}.
        Sources must stay under references/ or scripts/; each entry maps a
        source to the skills (second key segment) that receive it. Every
        save_skill_version of a mapped skill then copies the shared
        sources into that skill's repo at the SAME relative path and
        includes them in the commit — never hand-supply a mapped path in
        the save payload (the shared copy wins; the save rejects with an
        error listing the conflicting paths)."""
        _, client = await client_factory()
        body = {"files": files}
        return await client.call(
            "PUT", f"/workspaces/{quote(workspace_id, safe='')}/skills-shared", body
        )
