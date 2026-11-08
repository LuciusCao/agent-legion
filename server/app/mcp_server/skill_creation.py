"""Registration for the create_skill MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from server.app.mcp_server.skill_tool_common import ClientFactory


def register_create_skill_tool(mcp: FastMCP, client_factory: ClientFactory) -> None:
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
