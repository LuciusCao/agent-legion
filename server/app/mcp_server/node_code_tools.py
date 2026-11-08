"""Code-node read and draft-save tools for the studio-agent MCP server.

Registered onto the shared FastMCP instance from ``server.create_mcp_server``.
Both are loopback tools and stay ``async def`` for the single-event-loop reason
documented in ``server.py``. They save drafts only; publishing remains a human
action in Studio (STUDIO-AGENT-001).
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from server.app.mcp_server import local_files
from server.app.mcp_server.skill_tool_common import ClientFactory


def register_node_code_tools(mcp: FastMCP, client_factory: ClientFactory) -> None:
    # #749（开发者契约，不入工具 docstring——docstring 会进 LLM 上下文）：
    # save_node_code_draft 的响应携带刚写入草稿的 code_hash。本工具面永不发布
    # （STUDIO-AGENT-001），但人的发布流（检查器面板 / 聊天草稿卡）已用
    # expected_hash 做事务内 CAS 核对——将来任何工具侧发布必须带保存响应
    # 的 hash 作为 expected_hash（不匹配 409），绝不 hash-less 发布。
    @mcp.tool(structured_output=False)
    async def save_node_code_draft(
        workspace_id: str,
        node_key: str,
        code: str | None = None,
        change_note: str = "",
        expected_capability: str | None = None,
        code_path: str | None = None,
    ) -> str:
        """Save a draft of a code node's Python source (module-level run
        function required; get_authoring_guide §4). Draft only — a human
        publishes in Studio. expected_capability declares the capability you
        believe the node binds: mismatch with an existing node is rejected; a
        node absent from any published revision is accepted only WITH it
        (without it → 404). Supply exactly one of code or code_path. code_path
        reads UTF-8 bytes from data/studio-mcp-files/<workspace_id>/ on the
        MCP host (relative to that directory, or absolute within it).
        The response carries the saved draft's code_hash."""
        _, client = await client_factory()
        source = await local_files.load_code(client, workspace_id, code, code_path)
        body: dict[str, Any] = {"code": source, "change_note": change_note or None}
        if expected_capability is not None:
            body["expected_capability"] = expected_capability
        response = await client.call(
            "PUT",
            # workflows/{workflow_key} URL segment retired (#211): the
            # workspace-scoped path keys on workspace_id alone (key == id).
            f"/workspaces/{workspace_id}/nodes/{node_key}/code/draft",
            body,
        )
        return local_files.compact_response(response) if code_path is not None else response

    @mcp.tool(structured_output=False)
    async def get_node_code(
        workspace_id: str, node_key: str, output_path: str | None = None
    ) -> str:
        """Read a code node's current code state: builtin source, published
        custom code, any pending draft. output_path exports the full JSON to
        a NEW file in data/studio-mcp-files/<workspace_id>/ on the MCP host
        and returns only path/size/SHA-256. Parse JSON locally, select
        draft_code if present else code, edit and save with code_path."""
        _, client = await client_factory()
        response = await client.call(
            "GET",
            f"/workspaces/{workspace_id}/nodes/{node_key}/code",
        )
        return await local_files.export_response(workspace_id, output_path, response)
