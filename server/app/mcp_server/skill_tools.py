"""Skill tools for the studio-agent MCP server (issue #217).

Registered onto the shared FastMCP instance from ``server.create_mcp_server``
(split out for the file-size budget). All three are loopback tools and stay
``async def`` for the same single-event-loop reason documented in
``server.py``; all are draft-only: reads (``get_skill``, ``validate_skill``)
plus a local-repo commit+tag that never touches the DB skill lock.
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
    @mcp.tool()
    async def get_skill(skill_key: str, ref: str | None = None) -> str:
        """Read a skill: key, configured ref, the repo's git tags (latest
        version first), and text files (SKILL.md + references/ + scripts/).
        Without ref the content is the LOCKED commit when the skill lock pins
        one (the "current locked version"), else the working tree. Pass ref
        (a git tag of the skill repo) to preview that tag's content — e.g. a
        tag another agent just created — without changing the lock; an
        unknown tag comes back as a structured HTTP 404 error, nothing
        changes."""
        _, client = await client_factory()
        path = f"/skills/{_skill_path(skill_key)}"
        if ref is not None:
            path += f"?ref={quote(ref, safe='')}"
        return await client.call("GET", path)

    @mcp.tool()
    async def validate_skill(skill_key: str) -> str:
        """Check a skill against the runtime contract the platform enforces at
        dispatch: SKILL.md (non-empty) + references/output-contract.md +
        scripts/validate_output.py. Returns a structured error list
        ({"valid": bool, "errors": [{"path", "error"}]}). Persists nothing —
        always run this before save_skill_version."""
        _, client = await client_factory()
        return await client.call("POST", f"/skills/{_skill_path(skill_key)}/validate")

    @mcp.tool()
    async def save_skill_version(
        skill_key: str,
        files: list[dict[str, str]],
        new_tag: str,
        message: str,
    ) -> str:
        """Write a new version of a skill into its LOCAL source repo:
        validate every path (inside the skill dir, no '..' or absolute paths),
        write the files, re-run the contract check (failure rolls the repo
        back to its original commit), then git commit (author
        agent-legion-studio) and git tag new_tag. Local-path sources only —
        URL sources are refused. An existing tag is a conflict. The skill
        lock is never touched: running jobs keep the locked commit until a
        human reviews the diff, changes the ref, and relocks."""
        _, client = await client_factory()
        body: dict[str, Any] = {"files": files, "new_tag": new_tag, "message": message}
        return await client.call("POST", f"/skills/{_skill_path(skill_key)}/versions", body)
