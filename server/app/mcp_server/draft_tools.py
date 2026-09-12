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
    @mcp.tool()
    async def get_workflow_draft(workspace_id: str) -> str:
        """Get the workspace's unpublished workflow draft — the SAME canvas
        draft the human editor autosaves (not the active revision). Returns
        {"definition_yaml": ..., "updated_at": ...}; both null when no draft
        was ever saved (author against the active revision or from scratch).
        Pass the returned updated_at to save_workflow_draft as
        expected_updated_at; null updated_at means "never-saved"."""
        _, client = await client_factory()
        return await client.call("GET", f"/workspaces/{workspace_id}/workflow/draft")

    @mcp.tool()
    async def save_workflow_draft(
        workspace_id: str,
        definition_yaml: str,
        expected_updated_at: str,
    ) -> str:
        """Save the full workflow definition YAML as the workspace's Studio
        draft — the canvas and YAML editor pick it up live. CAS semantics:
        expected_updated_at must be the updated_at your last get_workflow_draft
        (or get_studio_context draft read) returned, or "never-saved" when no
        draft existed. A stale value is an HTTP 409 carrying the current draft
        (current_draft.definition_yaml + current_draft.updated_at) — re-read,
        rebase your changes onto it and retry; never retry with the old
        timestamp. Draft only: validate, compare, then request_workflow_publish
        — the human still confirms every publish."""
        _, client = await client_factory()
        body: dict[str, Any] = {
            "definition_yaml": definition_yaml,
            "expected_updated_at": expected_updated_at,
        }
        return await client.call("PUT", f"/workspaces/{workspace_id}/workflow/draft", body)
