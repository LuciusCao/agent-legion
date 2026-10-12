"""Workflow draft-store tools for the studio-agent MCP server (#633).

Registered onto the shared FastMCP instance from ``server.create_mcp_server``
(split out for the file-size budget, same pattern as ``prompt_tools``).
Both are loopback tools and stay ``async def`` for the single-event-loop
reason documented in ``server.py``. Draft-only: the save writes the SAME
canvas draft row the human editor autosaves to, under compare-and-set on
the draft generation (#1226 五轮收口) — a stale ``base_generation`` is a
409 carrying the current draft state (never a silent overwrite, and a
publish-deleted row is never resurrected by a pre-publish write);
publishing stays with ``request_workflow_publish`` and the human's confirm.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from server.app.mcp_server.skill_tools import ClientFactory


def register_draft_tools(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(structured_output=False)
    async def get_workflow_draft(workspace_id: str) -> str:
        """The workspace's unpublished workflow draft — the SAME canvas draft
        the human editor autosaves: {"definition_yaml", "updated_at",
        "definition_hash", "generation"}, yaml/hash null when never saved.
        Pass generation to save_workflow_draft as base_generation (0 when
        never saved; a publish that emptied the store advances it, so the
        empty-state read then carries the post-publish generation — pass it
        back as usual). definition_hash is
        the draft's semantic identity (parse → normalize → sha256): two YAMLs
        that differ only in serialization style (key order, explicit empty
        lists, block scalars) hash the same (#1143)."""
        _, client = await client_factory()
        return await client.call("GET", f"/workspaces/{workspace_id}/workflow/draft")

    @mcp.tool(structured_output=False)
    async def save_workflow_draft(
        workspace_id: str,
        definition_yaml: str,
        base_generation: int,
    ) -> str:
        """Save the full workflow definition YAML as the workspace's Studio
        draft (canvas picks it up live). CAS: base_generation must be the
        generation from your last get_workflow_draft — never invent one
        (stale → 409, negative → 422).
        A stale value → 409 carrying the current draft state
        (current_draft.definition_yaml + current_draft.generation +
        current_draft.definition_hash): rebase onto it and retry with its
        generation. The response echoes {"definition_yaml", "updated_at",
        "definition_hash", "generation"} — the semantic identity and new
        generation of what you just saved (#1143). Draft only — the human
        confirms every publish."""
        _, client = await client_factory()
        body: dict[str, Any] = {
            "definition_yaml": definition_yaml,
            "base_generation": base_generation,
        }
        return await client.call("PUT", f"/workspaces/{workspace_id}/workflow/draft", body)
