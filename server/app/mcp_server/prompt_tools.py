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
    @mcp.tool()
    async def get_node_prompt(
        workspace_id: str, node_key: str, definition_yaml: str | None = None
    ) -> str:
        """Preview the effective run prompt of an agent node: the fixed
        platform envelope plus the node instructions. execution.prompt empty
        means the platform auto-assembles default instructions from the
        node's label/capability/skill and declared inputs/outputs (is_default
        true, default_instructions shows the text); a non-empty
        execution.prompt REPLACES the default wholesale (custom_instructions
        echoes it). Pass definition_yaml to preview against a draft instead
        of the workspace's active revision. Read this BEFORE writing a custom
        prompt so the override builds on the real default."""
        _, client = await client_factory()
        body: dict[str, Any] = {"node_key": node_key}
        if definition_yaml is not None:
            body["definition_yaml"] = definition_yaml
        return await client.call("POST", f"/workspaces/{workspace_id}/node-prompt", body)

    @mcp.tool()
    async def save_node_prompt(workspace_id: str, node_key: str, prompt: str) -> str:
        """Write a custom prompt for one agent node into the workspace's
        unpublished workflow draft YAML (nodes.<key>.execution.prompt). The
        custom text replaces the auto-assembled default instructions
        wholesale — the platform envelope always stays. An empty string
        clears the custom prompt back to the auto default. Draft only — a
        human reviews and publishes the workflow in Studio."""
        _, client = await client_factory()
        return await client.call(
            "PUT",
            f"/workspaces/{workspace_id}/node-prompt",
            {"node_key": node_key, "prompt": prompt},
        )
