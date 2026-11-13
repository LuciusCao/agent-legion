"""Agent-definition and catalog-visibility tools for the studio-agent MCP
server (issue #633).

Registered onto the shared FastMCP instance from ``server.create_mcp_server``
(split out for the file-size budget, same pattern as ``workflow_tools``).
All are loopback tools and stay ``async def`` for the single-event-loop
reason documented in ``server.py``.

The historical Agent definitions and runtime/model catalogs are read-only
(STUDIO-AGENT-001). None of this group can edit worker-owned provider/model
declarations (EXEC-RUNTIME-MODELS-001) or the code-defined static tool catalog
(EXEC-RUNTIME-CATALOG-001), and none publishes (a human does that in Studio).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.app.mcp_server.skill_tool_common import ClientFactory


def register_agent_tools(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(structured_output=False)
    async def get_agent_definitions(workspace_id: str) -> str:
        """The workspace's historical Agent definitions (read-only; retired
        as node profile source, #440): latest version per agent with all
        fields + version metadata. New profiles go on the workflow node."""
        _, client = await client_factory()
        return await client.call("GET", f"/workspaces/{workspace_id}/agent-definitions")

    @mcp.tool(structured_output=False)
    async def get_runtime_models(workspace_id: str) -> str:
        """Available {runtime: {provider: [models]}} view from the workspace's
        ONLINE workers' declarations (discovery-only, never editable here).
        Pick node execution.* values a worker can actually claim."""
        _, client = await client_factory()
        return await client.call("GET", f"/workspaces/{workspace_id}/runtime-models")

    @mcp.tool(structured_output=False)
    async def get_agent_runtimes(workspace_id: str) -> str:
        """Runtime catalog: each runtime (pi, velites) with its agent tool
        catalog — names, tiers (default preselected / opt-in explicit /
        forced harness-enforced), parameters. Static — the editable surface
        is the agent node's tools list in the workflow draft."""
        _, client = await client_factory()
        return await client.call("GET", f"/workspaces/{workspace_id}/agent-runtimes")
