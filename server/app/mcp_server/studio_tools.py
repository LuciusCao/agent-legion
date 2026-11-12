"""Local authoring guide and session-context tools for the studio-agent MCP
server.

Registered onto the shared FastMCP instance from ``server.create_mcp_server``.
The guide stays synchronous because it only reads packaged text; the loopback
context tool stays asynchronous for the single-event-loop reason documented in
``server.py``.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.app.mcp_server.authoring_guide import guide_section
from server.app.mcp_server.skill_tool_common import ClientFactory


def register_studio_tools(
    mcp: FastMCP, client_factory: ClientFactory, *, session_bound: bool
) -> None:
    @mcp.tool(structured_output=False)
    def get_authoring_guide(section: str | None = None) -> str:
        """The built-in workflow authoring playbook. No section → FULL text;
        pass one chapter key: tool-map, flow, yaml, capabilities, agents,
        skills, errors. Read BEFORE authoring from scratch. Served locally —
        no backend call."""
        return guide_section(section)

    if session_bound:

        @mcp.tool(structured_output=False)
        async def get_studio_context() -> str:
            """Current Studio session context: bound workspace, its active
            workflow structure, the human's selected node, and the canvas'
            unpublished draft YAML (live every call). No workspace_id — the
            session binding decides. Call first for context."""
            config, client = await client_factory()
            if config.session_id is None:
                return "get_studio_context is unavailable: no chat session bound"
            return await client.call("GET", f"/chat-sessions/{config.session_id}/context")
