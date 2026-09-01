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
    @mcp.tool()
    def get_preview_guide() -> str:
        """The built-in preview panel playbook: the sandboxed iframe runtime
        (allow-scripts, never allow-same-origin), the read-only postMessage
        bridge contract (listArtifacts/readArtifact/getJobDetail + theme
        variables), the panel HTML skeleton, and the draft → human-publish
        flow. Read this BEFORE authoring a preview panel. Served locally —
        no backend call, always available."""
        return PREVIEW_GUIDE

    @mcp.tool()
    async def get_preview_context(workspace_id: str, job_id: str | None = None) -> str:
        """Real data shapes for authoring a preview panel: the workspace's
        recent jobs with their artifact inventories, plus bounded content
        samples (2k chars each, up to 5 artifacts) of one job — the given
        job_id, or the most recent job when omitted. Call this BEFORE writing
        a panel so the markup matches what jobs actually produce."""
        _, client = await client_factory()
        path = f"/workspaces/{workspace_id}/preview/context"
        if job_id is not None:
            path += f"?job_id={quote(job_id, safe='')}"
        return await client.call("GET", path)

    @mcp.tool()
    async def get_preview_panel(workspace_id: str) -> str:
        """Read the workspace's preview panel state: the published bundle
        (what job detail pages render) and any pending draft. Both null means
        the workspace falls back to the built-in generic preview."""
        _, client = await client_factory()
        return await client.call("GET", f"/workspaces/{workspace_id}/preview/panel")

    @mcp.tool()
    async def save_preview_panel_draft(workspace_id: str, html: str, change_note: str = "") -> str:
        """Save a preview panel draft: one self-contained HTML document
        (inline <style>/<script>, no external origins) that renders the job
        detail left column through the read-only bridge (see
        get_preview_guide). Validated for size and document shape. Draft
        only — a human reviews the live draft preview in the job detail page
        and publishes it there; the tool surface can never publish."""
        _, client = await client_factory()
        body: dict[str, Any] = {"html": html, "change_note": change_note or None}
        return await client.call("PUT", f"/workspaces/{workspace_id}/preview/panel/draft", body)
