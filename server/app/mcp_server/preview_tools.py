"""Preview panel tools for the studio-agent MCP server (issue #328).

Registered onto the shared FastMCP instance from ``server.create_mcp_server``
(split out for the file-size budget, same pattern as ``skill_tools`` /
``prompt_tools``). The HTTP-backed tools stay ``async def`` for the
single-event-loop reason documented in ``server.py``; ``get_preview_guide``
is served locally like ``get_authoring_guide``. All are draft-only: reads
(``get_preview_context``, ``get_preview_panel``) plus a draft write
(``save_preview_panel_draft``) — publishing a panel bundle is always a human
action on the secured route surface (STUDIO-AGENT-001).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP

from server.app.mcp_server.skill_tools import ClientFactory

PREVIEW_GUIDE = Path(__file__).with_name("preview_guide.md").read_text(encoding="utf-8")


def register_preview_tools(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(structured_output=False)
    def get_preview_guide() -> str:
        """The built-in preview panel playbook: sandboxed iframe runtime,
        read-only postMessage bridge contract, panel HTML skeleton, draft →
        human-publish flow. Read BEFORE authoring a panel. Served locally —
        no backend call."""
        return PREVIEW_GUIDE

    @mcp.tool(structured_output=False)
    async def get_preview_context(workspace_id: str, job_id: str | None = None) -> str:
        """Real data shapes for authoring a preview panel: recent jobs with
        artifact inventories + bounded content samples of one job (given
        job_id or the most recent). Call BEFORE writing a panel."""
        _, client = await client_factory()
        path = f"/workspaces/{workspace_id}/preview/context"
        if job_id is not None:
            path += f"?job_id={quote(job_id, safe='')}"
        return await client.call("GET", path)

    @mcp.tool(structured_output=False)
    async def get_preview_panel(workspace_id: str) -> str:
        """The workspace's preview panel state: published bundle (what job
        detail pages render) + any pending draft; both null → built-in
        generic preview."""
        _, client = await client_factory()
        return await client.call("GET", f"/workspaces/{workspace_id}/preview/panel")

    @mcp.tool(structured_output=False)
    async def save_preview_panel_draft(workspace_id: str, html: str, change_note: str = "") -> str:
        """Save a preview panel draft: one self-contained HTML document
        (inline <style>/<script>, no external origins) rendering the job
        detail left column via the read-only bridge (get_preview_guide).
        Draft only — a human publishes from the job detail page. The
        response carries html_hash — #749 contract note: the panel publish
        route has no expected_hash plumbing yet (the one publish path still
        on the None branch); when it gains CAS, the hash-consuming publish
        flow MUST assert the save response's hash, never publish hash-less."""
        _, client = await client_factory()
        body: dict[str, Any] = {"html": html, "change_note": change_note or None}
        return await client.call("PUT", f"/workspaces/{workspace_id}/preview/panel/draft", body)
