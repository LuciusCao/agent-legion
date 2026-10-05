"""Agent-definition and catalog-visibility tools for the studio-agent MCP
server (issue #633).

Registered onto the shared FastMCP instance from ``server.create_mcp_server``
(split out for the file-size budget, same pattern as ``workflow_tools``).
All are loopback tools and stay ``async def`` for the single-event-loop
reason documented in ``server.py``.

Everything here is read-only (STUDIO-AGENT-001): reading the workspace's
(historical) Agent definitions, discovering runtimes/tools and discovering
provider/models gives the authoring agent the visibility it needs to write
sensible drafts. #935 (#440 P3, D3): ``create_agent_definition`` is
deprecated — it writes nothing and returns ``AGENT_DEFINITION_RETIRED``,
steering the agent to the node execution profile in the workflow draft
(``save_workflow_draft``); P4 removes it. None of
it can edit the worker-owned provider/model declarations
(EXEC-RUNTIME-MODELS-001) or the code-defined static tool catalog
(EXEC-RUNTIME-CATALOG-001), and none of it publishes (a human does that in
Studio).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.app.mcp_server.skill_tools import ClientFactory

#: #935（#440 P3，D3）：Agent 定义写工具的统一引导返回值（不再写库）。
AGENT_DEFINITION_RETIRED = (
    "DEPRECATED (#440): Agent definitions no longer supply node execution"
    " profiles — nothing was written. Declare the profile on the workflow agent"
    " node itself and save it with save_workflow_draft: execution.runtime"
    " (pi | velites; or the workflow top-level execution.runtime default),"
    " tools, requires_labels, config_schema and skill (key + ref) on the node."
    " See get_authoring_guide §5 (node execution profile). get_agent_definitions"
    " stays available read-only for the historical definitions."
)


def register_agent_tools(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(structured_output=False)
    async def get_agent_definitions(workspace_id: str) -> str:
        """The workspace's historical Agent definitions (read-only; retired
        as node profile source, #440): latest version per agent with all
        fields + version metadata. New profiles go on the workflow node."""
        _, client = await client_factory()
        return await client.call("GET", f"/workspaces/{workspace_id}/agent-definitions")

    @mcp.tool(structured_output=False)
    async def create_agent_definition(
        workspace_id: str,
        capability: str,
        runtime: str,
        skill: str,
        tools: list[str] | None = None,
        requires_labels: dict[str, str] | None = None,
        config_schema: dict | None = None,
    ) -> str:
        """DEPRECATED — writes nothing. Agent execution profiles now live on
        the workflow agent node (execution.runtime, tools, requires_labels,
        config_schema, skill); edit them with save_workflow_draft."""
        del workspace_id, capability, runtime, skill, tools, requires_labels, config_schema
        return AGENT_DEFINITION_RETIRED

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
