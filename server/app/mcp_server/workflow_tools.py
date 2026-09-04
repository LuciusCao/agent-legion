"""Workflow tools for the studio-agent MCP server.

Registered onto the shared FastMCP instance from ``server.create_mcp_server``
(split out for the file-size budget, same pattern as ``prompt_tools``).
All are loopback tools and stay ``async def`` for the single-event-loop
reason documented in ``server.py``.

Safety invariant (#416, STUDIO-AGENT-001): ``request_workflow_publish``
NEVER publishes — it parks a pending request the human confirms in Studio's
publish review dialog; the confirm endpoint replays the manual publish gates.
The other three are reads / validation-only and persist nothing.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.app.mcp_server.skill_tools import ClientFactory


def register_workflow_tools(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool()
    async def get_active_workflow(workspace_id: str) -> str:
        """Get the active workflow revision of a workspace, including the full
        definition YAML. Read this before drafting changes so the draft builds
        on what is actually live. No published workflow yet yields a structured
        empty state ({"state": "empty"}) instead of an error — the signal to
        start the from-scratch flow (see get_authoring_guide)."""
        _, client = await client_factory()
        return await client.call("GET", f"/workspaces/{workspace_id}/workflow/active")

    @mcp.tool()
    async def validate_workflow(workspace_id: str, definition_yaml: str) -> str:
        """Validate a workflow definition YAML draft against the publish
        validation set. Persists nothing. Always validate a draft before
        asking the human to review or apply it."""
        _, client = await client_factory()
        return await client.call(
            "POST",
            f"/workspaces/{workspace_id}/workflow/validate",
            {"definition_yaml": definition_yaml},
        )

    @mcp.tool()
    async def compare_workflow(workspace_id: str, definition_yaml: str) -> str:
        """Diff a workflow definition YAML draft against the workspace's active
        revision: per-node changes, risk summary, whether it would create a new
        revision. With no published baseline the result is a full-draft preview
        (everything added, base_revision null). Persists nothing."""
        _, client = await client_factory()
        return await client.call(
            "POST",
            f"/workspaces/{workspace_id}/workflow/compare",
            {"definition_yaml": definition_yaml},
        )

    @mcp.tool()
    async def request_workflow_publish(workspace_id: str) -> str:
        """Ask the human to publish the workspace's unpublished workflow draft
        (the canvas draft — same YAML get_studio_context reports). SAFETY:
        this NEVER publishes by itself. It parks a pending request; the human
        sees the publish review dialog in Studio (with the compare summary)
        and confirms or cancels. The draft must already pass full validation —
        a draft with errors returns HTTP 409 and creates no request. After
        calling, tell the human to review the dialog, then poll
        get_publish_request_status for the outcome."""
        _, client = await client_factory()
        return await client.call("POST", f"/workspaces/{workspace_id}/workflow/publish-request")

    @mcp.tool()
    async def get_publish_request_status(request_id: str) -> str:
        """Poll the outcome of a request_workflow_publish call: the request's
        status (pending/confirming/confirmed/rejected/expired/superseded),
        expires_at, and result_revision_id when the human confirmed and the
        publish created a NEW revision (null for runtime-only config
        updates). confirming means the human pressed confirm and the publish
        is in flight. A confirmed request means the draft is live; rejected
        or expired means you may revise the draft and re-request; superseded
        means displaced by a newer request or a manual human publish (check
        the active revision — a manual publish still means live)."""
        _, client = await client_factory()
        return await client.call("GET", f"/publish-requests/{request_id}")
