"""Workflow tools for the studio-agent MCP server.

Registered onto the shared FastMCP instance from ``server.create_mcp_server``
(split out for the file-size budget, same pattern as ``prompt_tools``).
All are loopback tools and stay ``async def`` for the single-event-loop
reason documented in ``server.py``.

Safety invariant (#416, STUDIO-AGENT-001): ``request_workflow_publish``
NEVER publishes — it parks a pending request the human confirms in Studio's
publish review dialog; the confirm endpoint replays the manual publish gates.
The other tools are reads / validation-only and persist nothing. The #633
draft read/write pair lives in ``draft_tools`` (split for the budget).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.app.mcp_server.skill_tools import ClientFactory


def register_workflow_tools(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(structured_output=False)
    async def get_active_workflow(workspace_id: str) -> str:
        """The workspace's active workflow revision with the full definition
        YAML — read before drafting changes. No published workflow yet →
        {"state": "empty"}: the from-scratch signal, not an error."""
        _, client = await client_factory()
        return await client.call("GET", f"/workspaces/{workspace_id}/workflow/active")

    @mcp.tool(structured_output=False)
    async def validate_workflow(workspace_id: str, definition_yaml: str) -> str:
        """Validate a workflow definition YAML draft against the publish
        validation set. Persists nothing — always validate before asking the
        human to review."""
        _, client = await client_factory()
        return await client.call(
            "POST",
            f"/workspaces/{workspace_id}/workflow/validate",
            {"definition_yaml": definition_yaml},
        )

    @mcp.tool(structured_output=False)
    async def compare_workflow(workspace_id: str, definition_yaml: str) -> str:
        """Diff a workflow YAML draft against the active revision: per-node
        changes, risk summary, new-revision preview. No published baseline →
        full-draft preview (base_revision null). Persists nothing."""
        _, client = await client_factory()
        return await client.call(
            "POST",
            f"/workspaces/{workspace_id}/workflow/compare",
            {"definition_yaml": definition_yaml},
        )

    @mcp.tool(structured_output=False)
    async def request_workflow_publish(workspace_id: str) -> str:
        """Ask the human to publish the workspace's unpublished workflow
        draft. SAFETY: NEVER publishes by itself — parks a pending request;
        the human confirms or cancels in Studio's publish review dialog. A
        draft with validation errors → HTTP 409, no request created. Then
        tell the human to review the dialog and poll
        get_publish_request_status."""
        _, client = await client_factory()
        return await client.call("POST", f"/workspaces/{workspace_id}/workflow/publish-request")

    @mcp.tool(structured_output=False)
    async def get_publish_request_status(request_id: str) -> str:
        """Poll a publish request's outcome: pending/confirming/confirmed/
        rejected/expired/superseded, expires_at, and result_revision_id when
        the confirm created a NEW revision (null for runtime-only updates).
        superseded = displaced by a newer request or a manual human publish —
        check the active revision."""
        _, client = await client_factory()
        return await client.call("GET", f"/publish-requests/{request_id}")
