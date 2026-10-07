"""Thin MCP wrapper over the studio-agent tool surface.

Tools forward to the Agent Legion backend via ``tool_client.ToolClient``
(studio-agent scoped token, STUDIO-AGENT-001) and return the response body
as text; non-2xx comes back as ``HTTP <code>: <body>`` text instead of
raising. Only ``get_authoring_guide`` is served locally (no HTTP call).

Two transports share this registration: the stdio entry point
(``python -m server.app.mcp_server``, external self-service agents, static
env config) and the in-app streamable-HTTP endpoint (``http_app.py``, Studio
chat sessions, per-request header config). Both pass a config resolver; the
HTTP one re-resolves on every tool call so each request runs under its own
scoped token and session binding. Loopback tools are ``async def`` over the
fully-async ``ToolClient.call`` (httpx): the HTTP transport executes tools
on the uvicorn event loop, so a sync tool deadlocks the single-worker
backend against its own request, and a thread-pool offload can starve the
loopback's sync handlers sharing that pool (``get_authoring_guide`` stays
sync: it never blocks).
"""

from __future__ import annotations

import sys
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

# Workflow read/validate/compare/publish-request tools (issue #416 grouped
# them with their draft lifecycle) live in workflow_tools; skill/prompt/
# preview/job tools in their sibling modules (file-size budget).
from server.app.mcp_server import (
    agent_tools,
    draft_tools,
    job_tools,
    local_files,
    preview_tools,
    prompt_tools,
    schema_slim,
    shared_tools,
    skill_tools,
    workflow_tools,
)
from server.app.mcp_server.agent_tools import AGENT_DEFINITION_RETIRED
from server.app.mcp_server.authoring_guide import guide_section
from server.app.mcp_server.config import McpConfigError, McpServerConfig
from server.app.mcp_server.tool_client import ToolClient

ConfigResolver = Callable[[], Awaitable[McpServerConfig]]


def create_mcp_server(config: McpServerConfig | ConfigResolver) -> FastMCP:
    """Build the FastMCP server exposing the studio-agent tools."""
    if callable(config):
        resolve = config
    else:

        async def _static() -> McpServerConfig:
            return config

        resolve = _static
    mcp = FastMCP("agent-legion-studio")
    # #660 phase C: static external configs without a chat-session binding
    # never register the two session-bound tools — unbound they can only
    # answer "unavailable". The callable resolver (HTTP transport) always
    # registers them: the binding arrives per request header.
    session_bound = callable(config) or config.session_id is not None

    async def _client() -> tuple[McpServerConfig, ToolClient]:
        # Awaiting the resolver lets the HTTP transport's per-request config
        # rebuild offload its blocking pieces (registry DB read) to a worker
        # thread instead of stalling the uvicorn loop (#158 review).
        return (resolved := await resolve()), ToolClient(resolved)

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
            config, client = await _client()
            if config.session_id is None:
                return "get_studio_context is unavailable: no chat session bound"
            return await client.call("GET", f"/chat-sessions/{config.session_id}/context")

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
        _, client = await _client()
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
        _, client = await _client()
        response = await client.call(
            "GET",
            f"/workspaces/{workspace_id}/nodes/{node_key}/code",
        )
        return await local_files.export_response(workspace_id, output_path, response)

    # #935（#440 P3，D3）：Agent 定义写工具 deprecate——不再写库，返回
    # 改用 save_workflow_draft 写节点执行档案的引导；P4 删除（tool_names
    # 契约同步）。
    @mcp.tool(structured_output=False)
    async def save_agent_definition_draft(
        workspace_id: str,
        agent_id: str,
        capability: str,
        runtime: str,
        skill: str,
        tools: list[str] | None = None,
        requires_labels: dict[str, str] | None = None,
        config_schema: dict | None = None,
    ) -> str:
        """DEPRECATED — writes nothing. Agent execution profiles now live on
        the workflow agent node (execution.runtime, tools, requires_labels,
        config_schema, skill); edit them with save_workflow_draft."""
        del workspace_id, agent_id, capability, runtime, skill
        del tools, requires_labels, config_schema
        return AGENT_DEFINITION_RETIRED

    # Skill read/validate/save-version tools (issue #217) and node prompt
    # preview/save tools, both split into sibling modules for the budget;
    # shared-material tools (#633) sit in their own sibling module too.
    #
    # #678: every tool registered below (inline here and in the eight
    # register_* siblings) is mirrored BY NAME in tool_names.py — the
    # authoritative manifest studio_chat/prompts.py imports to recognize
    # agent-legion tool calls (permission auto-approve). Adding, renaming,
    # or removing a tool means updating that manifest in the same change;
    # tests/mcp_server/test_tool_names.py pins the three-way equality
    # (registered == manifest == prompts reference). No runtime assert
    # here on purpose: FastMCP 1.x's only public tool listing is async
    # ``list_tools()`` (create_mcp_server runs on a live event loop for
    # the HTTP transport, which rebuilds the instance per request), and
    # the sync path would mean reaching into pinned-private internals —
    # the contract test is the enforcement point instead.
    skill_tools.register_skill_tools(mcp, _client)
    shared_tools.register_shared_tools(mcp, _client)
    prompt_tools.register_prompt_tools(mcp, _client)
    # Workflow tools (active read / validate / compare / publish-request,
    # issue #416): the publish request parks a pending publish for the human
    # to confirm in Studio — never publishes directly. Draft read/write pair
    # (issue #633): canvas-draft CAS editing, same draft-only boundary.
    workflow_tools.register_workflow_tools(mcp, _client)
    draft_tools.register_draft_tools(mcp, _client)
    # Preview panel tools (issue #328): context/panel reads + draft save,
    # draft-only like the rest of the surface.
    preview_tools.register_preview_tools(mcp, _client)
    # Agent-definition/catalog tools (issue #633): read the workspace's agent
    # definitions and discover runtimes/tools and provider/models — all
    # read-only visibility (worker-owned models and the static tool catalog
    # are never editable from the tool surface).
    agent_tools.register_agent_tools(mcp, _client)
    # Job observation tools (issue #329): read-only diagnosis surface —
    # context/detail/logs/artifacts/list/compare; no effecting operations.
    job_tools.register_job_tools(mcp, _client, session_bound=session_bound)

    # #660 phase A2: cosmetic de-fatting of the FastMCP-generated input
    # schemas (per-parameter titles, explicit null defaults). tools/call
    # validation runs on fn_metadata's pydantic model, not on the parameters
    # dict, so rewriting the listing schema cannot change call semantics.
    schema_slim.slim_tool_parameters(mcp)
    return mcp


def main() -> None:
    try:
        config = McpServerConfig.from_env()
    except McpConfigError as exc:
        print(f"agent-legion-mcp: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    create_mcp_server(config).run()
