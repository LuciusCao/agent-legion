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

from server.app.mcp_server import local_files
from server.app.mcp_server.skill_tools import ClientFactory
from server.app.services.preview_panels import MAX_BUNDLE_BYTES

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
    async def get_preview_panel(workspace_id: str, output_path: str | None = None) -> str:
        """The workspace's preview panel state: published bundle (what job
        detail pages render) + any pending draft; both null → built-in
        generic preview. output_path exports the full JSON to a NEW file
        in data/studio-mcp-files/<workspace_id>/ on the MCP host and
        returns only path/size/SHA-256 — extract the html, edit locally,
        and save back with html_path."""
        _, client = await client_factory()
        response = await client.call("GET", f"/workspaces/{workspace_id}/preview/panel")
        return await local_files.export_response(workspace_id, output_path, response)

    # #749（开发者契约，不入工具 docstring——docstring 会进 LLM 上下文）：
    # save_preview_panel_draft 的响应携带刚写入草稿的 html_hash。本工具面
    # 永不发布（STUDIO-AGENT-001）；人的发布路由自 #841 起必填
    # expected_hash（事务内 CAS，不匹配 409）——将来任何工具侧发布必须
    # 带保存响应的 hash，store 层已无 hash-less 发布分支。
    @mcp.tool(structured_output=False)
    async def save_preview_panel_draft(
        workspace_id: str,
        html: str | None = None,
        change_note: str = "",
        html_path: str | None = None,
    ) -> str:
        """Save a preview panel draft: one self-contained HTML document
        (inline <style>/<script>, no external origins, no inline on*=
        event-handler attributes — use addEventListener) rendering the job
        detail left column via the read-only bridge (get_preview_guide).
        Draft only — a human publishes from the job detail page. Supply
        exactly one of html or html_path. html_path reads UTF-8 bytes from
        data/studio-mcp-files/<workspace_id>/ on the MCP host (relative to
        that directory, or absolute within it) under the same 256 KiB
        ceiling as inline html. The response carries the saved draft's
        html_hash."""
        _, client = await client_factory()
        source = await local_files.load_code(
            client,
            workspace_id,
            html,
            html_path,
            param_names=("html", "html_path"),
            max_bytes=MAX_BUNDLE_BYTES,
        )
        body: dict[str, Any] = {"html": source, "change_note": change_note or None}
        response = await client.call("PUT", f"/workspaces/{workspace_id}/preview/panel/draft", body)
        return local_files.compact_response(response) if html_path is not None else response
