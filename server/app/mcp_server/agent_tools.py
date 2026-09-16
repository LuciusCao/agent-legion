"""Agent-definition and catalog-visibility tools for the studio-agent MCP
server (issue #633).

Registered onto the shared FastMCP instance from ``server.create_mcp_server``
(split out for the file-size budget, same pattern as ``workflow_tools``).
All are loopback tools and stay ``async def`` for the single-event-loop
reason documented in ``server.py``.

Everything here is read-only or draft-only (STUDIO-AGENT-001): reading the
workspace's Agent definitions, starting a NEW Agent definition draft,
discovering runtimes/tools and discovering provider/models gives the
authoring agent the visibility it needs to write sensible drafts — none of
it can edit the worker-owned provider/model declarations
(EXEC-RUNTIME-MODELS-001) or the code-defined static tool catalog
(EXEC-RUNTIME-CATALOG-001), and none of it publishes (a human does that in
Studio).
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from server.app.agent_catalog.definition import DEFAULT_TOOLS
from server.app.mcp_server.skill_tools import ClientFactory


def register_agent_tools(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(structured_output=False)
    async def get_agent_definitions(workspace_id: str) -> str:
        """The workspace's Agent definitions: latest version per agent
        (pending draft beats published), ALL fields (capability, runtime,
        skill, tools, requires_labels, config_schema) + version metadata.
        Read before drafting agent or workflow changes."""
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
        """Start a NEW Agent definition draft: the agent_id derives from the
        capability, so a capability that already has an Agent (any status)
        returns HTTP 409 naming it — edit it with save_agent_definition_draft
        instead. Same FULL-PAYLOAD semantics as the save (get_agent_definitions
        first, echo the values you want kept). Draft only — a human publishes
        it in Studio."""
        body: dict[str, Any] = {
            "capability": capability,
            "runtime": runtime,
            "skill": skill,
            # #476：默认三件套与 AgentDefinition 同源（catalog default 档）。
            "tools": tools or list(DEFAULT_TOOLS),
            "requires_labels": requires_labels or {},
            "config_schema": config_schema or {},
        }
        _, client = await client_factory()
        return await client.call("POST", f"/workspaces/{workspace_id}/agent-definitions", body)

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
        is the tools selection in an Agent definition draft."""
        _, client = await client_factory()
        return await client.call("GET", f"/workspaces/{workspace_id}/agent-runtimes")
