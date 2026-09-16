"""Skill tools for the studio-agent MCP server (issue #217).

Registered onto the shared FastMCP instance from ``server.create_mcp_server``
(split out for the file-size budget). All four are loopback tools and stay
``async def`` for the same single-event-loop reason documented in
``server.py``; all are draft-only: reads (``get_skill``, ``validate_skill``),
a local-repo commit+tag that never touches the DB skill lock
(``save_skill_version``), and repo creation under the workspace's skill dir
that equally never touches the lock (``create_skill``, #633).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP

from server.app.mcp_server.config import McpServerConfig
from server.app.mcp_server.tool_client import ToolClient

ClientFactory = Callable[[], Awaitable[tuple[McpServerConfig, ToolClient]]]


def _skill_path(skill_key: str) -> str:
    """URL-encode each skill-key segment (keys are <group>/<name>)."""
    return "/".join(quote(segment, safe="") for segment in skill_key.split("/"))


def register_skill_tools(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(structured_output=False)
    async def get_skill(skill_key: str, ref: str | None = None) -> str:
        """Read a skill: key, git tags (latest first), text files (SKILL.md +
        references/ + scripts/). No ref → working tree at HEAD (the latest
        semantics); ref previews one tag without moving the lock (unknown tag
        → 404)."""
        _, client = await client_factory()
        path = f"/skills/{_skill_path(skill_key)}"
        if ref is not None:
            path += f"?ref={quote(ref, safe='')}"
        return await client.call("GET", path)

    @mcp.tool(structured_output=False)
    async def validate_skill(skill_key: str) -> str:
        """Check a skill against the dispatch-time runtime contract (SKILL.md
        + references/output-contract.md + scripts/validate_output.py + root
        contract.yaml parse). Returns {valid, errors, warnings} — a MISSING
        contract.yaml only warns. Persists nothing; run before
        save_skill_version."""
        _, client = await client_factory()
        return await client.call("POST", f"/skills/{_skill_path(skill_key)}/validate")

    @mcp.tool(structured_output=False)
    async def save_skill_version(
        skill_key: str,
        files: list[dict[str, str]],
        new_tag: str,
        message: str,
    ) -> str:
        """Write a new skill version into its LOCAL in-place repo: paths
        validated (inside the skill dir, no '..'/absolute), contract
        re-checked (malformed root contract.yaml rolls the repo back; missing
        only warns), then commit + tag new_tag (existing tag = conflict).
        Skill lock untouched — pinned nodes keep the locked commit, latest
        nodes follow the new HEAD; a human reviews, re-pins, relocks."""
        _, client = await client_factory()
        body: dict[str, Any] = {"files": files, "new_tag": new_tag, "message": message}
        return await client.call("POST", f"/skills/{_skill_path(skill_key)}/versions", body)

    @mcp.tool(structured_output=False)
    async def create_skill(
        workspace_id: str,
        skill_name: str,
        files: list[dict[str, str]],
        new_tag: str,
        message: str,
    ) -> str:
        """Create a BRAND-NEW skill repo under the workspace's skill
        directory. skill_name: one segment (^[a-z0-9][a-z0-9_-]{0,63}$);
        files MUST carry the four-file contract set (SKILL.md +
        references/output-contract.md + scripts/validate_output.py + root
        contract.yaml) or 422. All-or-nothing; initial commit tagged new_tag;
        lock untouched. Iterate with validate_skill / save_skill_version."""
        _, client = await client_factory()
        body: dict[str, Any] = {
            "skill_name": skill_name,
            "files": files,
            "new_tag": new_tag,
            "message": message,
        }
        return await client.call("POST", f"/workspaces/{workspace_id}/skills", body)
