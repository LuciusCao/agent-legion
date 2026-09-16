"""Node prompt tools for the studio-agent MCP server.

Registered onto the shared FastMCP instance from ``server.create_mcp_server``
(split out for the file-size budget, same pattern as ``skill_tools``). Both
are loopback tools and stay ``async def`` for the single-event-loop reason
documented in ``server.py``; both are draft-only: ``get_node_prompt`` previews
the effective prompt, ``save_node_prompt`` edits the workspace's unpublished
draft YAML — publishing stays a human action.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from server.app.mcp_server.skill_tools import ClientFactory


def register_prompt_tools(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(structured_output=False)
    async def get_node_prompt(
        workspace_id: str, node_key: str, definition_yaml: str | None = None
    ) -> str:
        """Preview an agent node's effective run prompt: platform envelope +
        node instructions (auto-assembled default, or a custom
        execution.prompt REPLACING it wholesale). Pass definition_yaml to
        preview against a draft. Read BEFORE writing a custom prompt."""
        _, client = await client_factory()
        body: dict[str, Any] = {"node_key": node_key}
        if definition_yaml is not None:
            body["definition_yaml"] = definition_yaml
        return await client.call("POST", f"/workspaces/{workspace_id}/node-prompt", body)

    @mcp.tool(structured_output=False)
    async def save_node_prompt(workspace_id: str, node_key: str, prompt: str) -> str:
        """Write a custom prompt for one agent node into the unpublished
        workflow draft YAML (nodes.<key>.execution.prompt); replaces the auto
        default wholesale, empty string clears back to default. Draft only —
        a human publishes the workflow."""
        _, client = await client_factory()
        return await client.call(
            "PUT",
            f"/workspaces/{workspace_id}/node-prompt",
            {"node_key": node_key, "prompt": prompt},
        )
