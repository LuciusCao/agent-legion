"""Workflow draft-store tools for the studio-agent MCP server (#633).

Registered onto the shared FastMCP instance from ``server.create_mcp_server``
(split out for the file-size budget, same pattern as ``prompt_tools``).
Both are loopback tools and stay ``async def`` for the single-event-loop
reason documented in ``server.py``. Draft-only: the save writes the SAME
canvas draft row the human editor autosaves to, under compare-and-set
semantics — a stale ``updated_at`` is a 409 carrying the current draft
(never a silent overwrite); publishing stays with
``request_workflow_publish`` and the human's confirm.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from server.app.mcp_server.skill_tools import ClientFactory


def register_draft_tools(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(structured_output=False)
    async def get_workflow_draft(workspace_id: str) -> str:
        """The workspace's unpublished workflow draft — the SAME canvas draft
        the human editor autosaves: {"definition_yaml", "updated_at"}, both
        null when never saved. Pass updated_at to save_workflow_draft as
        expected_updated_at; null means "never-saved"."""
        _, client = await client_factory()
        return await client.call("GET", f"/workspaces/{workspace_id}/workflow/draft")

    @mcp.tool(structured_output=False)
    async def save_workflow_draft(
        workspace_id: str,
        definition_yaml: str,
        expected_updated_at: str,
    ) -> str:
        """Save the full workflow definition YAML as the workspace's Studio
        draft (canvas picks it up live). CAS: expected_updated_at must be the
        updated_at from your last get_workflow_draft, get_studio_context's
        draft_updated_at, or the literal "never-saved" — never invent a
        timestamp (wrong → 409, malformed → 422). A stale value → 409
        carrying the current draft (current_draft.definition_yaml +
        current_draft.updated_at): rebase onto it and retry with its
        updated_at. Draft only — the human confirms every publish."""
        _, client = await client_factory()
        body: dict[str, Any] = {
            "definition_yaml": definition_yaml,
            "expected_updated_at": expected_updated_at,
        }
        return await client.call("PUT", f"/workspaces/{workspace_id}/workflow/draft", body)
