"""Deprecated Agent-definition write tools for the studio-agent MCP server.

#935 (#440 P3, D3): both tools write nothing and return the same guidance to
edit the node execution profile through ``save_workflow_draft``. Their separate
registration functions preserve the composition root's historical tool order.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

#: #935（#440 P3，D3）：Agent 定义写工具的统一引导返回值（不再写库）。
AGENT_DEFINITION_RETIRED = (
    "DEPRECATED (#440): Agent definitions no longer supply node execution"
    " profiles — nothing was written. Declare the profile on the workflow agent"
    " node itself and save it with save_workflow_draft: execution.runtime"
    " (pi | velites), provider/model/thinking, tools, requires_labels,"
    " config_schema and skill (key + ref) on that same node. Workflow-level"
    " execution defaults are retired (#805) and rejected on save."
    " See get_authoring_guide §5 (node execution profile). get_agent_definitions"
    " stays available read-only for the historical definitions."
)


def register_save_agent_definition_draft_tool(mcp: FastMCP) -> None:
    @mcp.tool(structured_output=False)
    async def save_agent_definition_draft(
        workspace_id: str,
        agent_id: str,
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
        del workspace_id, agent_id, capability, runtime, skill
        del tools, requires_labels, config_schema
        return AGENT_DEFINITION_RETIRED


def register_create_agent_definition_tool(mcp: FastMCP) -> None:
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
