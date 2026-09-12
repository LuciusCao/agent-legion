"""Agent-definition and catalog-visibility tools for the studio-agent MCP
server (issue #633).

Registered onto the shared FastMCP instance from ``server.create_mcp_server``
(split out for the file-size budget, same pattern as ``workflow_tools``).
All are loopback tools and stay ``async def`` for the single-event-loop
reason documented in ``server.py``.

Everything here is read-only or draft-only (STUDIO-AGENT-001): reading the
workspace's Agent definitions, discovering runtimes/tools and discovering
provider/models gives the authoring agent the visibility it needs to write
sensible drafts — none of it can edit the worker-owned provider/model
declarations (EXEC-RUNTIME-MODELS-001) or the code-defined static tool
catalog (EXEC-RUNTIME-CATALOG-001).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.app.mcp_server.skill_tools import ClientFactory


def register_agent_tools(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool()
    async def get_agent_definitions(workspace_id: str) -> str:
        """List the workspace's Agent definitions — the latest version per
        agent (a pending draft beats the published row) with ALL fields:
        capability, runtime, skill, tools, requires_labels, config_schema,
        plus version metadata (version, status, definition_hash, created_by,
        created_at, published_at). Read this before drafting agent or
        workflow changes so capability bindings build on what exists."""
        _, client = await client_factory()
        return await client.call("GET", f"/workspaces/{workspace_id}/agent-definitions")

    @mcp.tool()
    async def get_runtime_models(workspace_id: str) -> str:
        """The workspace's available {runtime: {provider: [models]}} view,
        aggregated from the workspace's ONLINE workers' declarations. This is
        discovery-only: provider/model declarations are worker-owned and are
        NEVER editable through these tools (EXEC-RUNTIME-MODELS-001). Use the
        view to pick sensible node `execution.*` values (a typed value
        corresponds to a worker that can actually claim the execution)."""
        _, client = await client_factory()
        return await client.call("GET", f"/workspaces/{workspace_id}/runtime-models")

    @mcp.tool()
    async def get_agent_runtimes(workspace_id: str) -> str:
        """The runtime catalog: each runtime (pi, velites) and its agent tool
        catalog — every tool's name, tier (default = preselected, opt-in =
        explicitly enabled, forced = harness-enforced with an activation
        condition) and parameters. The catalog is a code-defined static
        projection (EXEC-RUNTIME-CATALOG-001): agent "tools" are not
        runtime-editable; the editable surface is the `tools` selection
        inside an Agent definition draft."""
        _, client = await client_factory()
        return await client.call("GET", f"/workspaces/{workspace_id}/agent-runtimes")
