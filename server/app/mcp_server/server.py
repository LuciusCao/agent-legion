"""Thin MCP wrapper over the studio-agent tool surface.

Agent Legion backend via ``tool_client.ToolClient``, authenticated with a
studio-agent scoped token (STUDIO-AGENT-001). Tools return the response body
as text; non-2xx responses come back as ``HTTP <code>: <body>`` text instead
of raising. The only exception is ``get_authoring_guide``, which serves the
built-in authoring playbook (``authoring_guide.AUTHORING_GUIDE``) locally
without an HTTP call.

Two transports share this registration: the stdio entry point
(``python -m server.app.mcp_server``, external self-service agents, static
env config) and the in-app streamable-HTTP endpoint (``http_app.py``, Studio
chat sessions, per-request header config). Both pass a config resolver; the
HTTP one re-resolves on every tool call so each request runs under its own
scoped token and session binding. Loopback tools are ``async def`` and go
through the fully-async ``ToolClient.call`` (httpx): the HTTP transport
executes tools on the uvicorn event loop, so the loopback must be true async
I/O — a sync tool deadlocks the single-worker backend against its own
request, and a thread-pool offload can starve the loopback's sync handlers
sharing that pool (``get_authoring_guide`` stays sync: it never blocks).
"""

from __future__ import annotations

import sys
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

from server.app.mcp_server.authoring_guide import AUTHORING_GUIDE
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

    async def _client() -> tuple[McpServerConfig, ToolClient]:
        # Awaiting the resolver lets the HTTP transport's per-request config
        # rebuild offload its blocking pieces (registry DB read) to a worker
        # thread instead of stalling the uvicorn loop (#158 review).
        resolved = await resolve()
        return resolved, ToolClient(resolved)

    @mcp.tool()
    def get_authoring_guide() -> str:
        """The built-in workflow authoring playbook: capability naming, the
        workflow YAML schema, code-node vs agent-node resolution, the
        draft → validate → compare → human-publish flow, and common errors.
        Read this BEFORE authoring from scratch. Served locally — no backend
        call, always available."""
        return AUTHORING_GUIDE

    @mcp.tool()
    async def get_studio_context() -> str:
        """Current Studio session context: the bound workspace, its active
        workflow structure (nodes and capabilities), and the node the human
        currently has selected in Studio (live value on every call). Takes no
        workspace_id — the session binding decides which workspace you operate
        on. Call this first when you need workspace or selection context."""
        config, client = await _client()
        if config.session_id is None:
            return "get_studio_context is unavailable: no chat session bound"
        return await client.call("GET", f"/chat-sessions/{config.session_id}/context")

    @mcp.tool()
    async def get_active_workflow(workspace_id: str) -> str:
        """Get the active workflow revision of a workspace, including the full
        definition YAML. Read this before drafting changes so the draft builds
        on what is actually live. No published workflow yet yields a structured
        empty state ({"state": "empty"}) instead of an error — the signal to
        start the from-scratch flow (see get_authoring_guide)."""
        _, client = await _client()
        return await client.call("GET", f"/workspaces/{workspace_id}/workflow/active")

    @mcp.tool()
    async def validate_workflow(workspace_id: str, definition_yaml: str) -> str:
        """Validate a workflow definition YAML draft against the publish
        validation set. Persists nothing. Always validate a draft before
        asking the human to review or apply it."""
        _, client = await _client()
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
        _, client = await _client()
        return await client.call(
            "POST",
            f"/workspaces/{workspace_id}/workflow/compare",
            {"definition_yaml": definition_yaml},
        )

    @mcp.tool()
    async def save_node_code_draft(
        workspace_id: str,
        workflow_key: str,
        node_key: str,
        code: str,
        change_note: str = "",
        expected_capability: str | None = None,
    ) -> str:
        """Save a draft of a code node's Python source (the module must expose
        ``run(job, job_dir, runtime)``). Draft only — a human reviews and
        publishes it in Studio. expected_capability declares the capability you
        believe the node binds: a mismatch with an existing node is rejected;
        a node absent from any published revision is accepted only with it
        (skeleton draft ahead of the workflow draft introducing the node)."""
        body: dict[str, Any] = {"code": code, "change_note": change_note or None}
        if expected_capability is not None:
            body["expected_capability"] = expected_capability
        _, client = await _client()
        return await client.call(
            "PUT",
            f"/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code/draft",
            body,
        )

    @mcp.tool()
    async def get_node_code(workspace_id: str, workflow_key: str, node_key: str) -> str:
        """Read the current code state of a workflow code node: builtin source,
        published custom code, and any pending draft."""
        _, client = await _client()
        return await client.call(
            "GET",
            f"/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code",
        )

    @mcp.tool()
    async def save_agent_definition_draft(
        workspace_id: str,
        agent_id: str,
        capability: str,
        runtime: str,
        skill: str,
        tools: list[str] | None = None,
    ) -> str:
        """Save a draft Agent definition (workspace-scoped) binding a capability
        to a runtime and skill. runtime is one of: pi, openclaw, velites.
        Draft only — a human publishes it in Studio before any job can use it."""
        _, client = await _client()
        return await client.call(
            "PUT",
            f"/workspaces/{workspace_id}/agent-definitions/{agent_id}/draft",
            {
                "capability": capability,
                "runtime": runtime,
                "skill": skill,
                "tools": tools or ["read", "write", "bash"],
            },
        )

    return mcp


def main() -> None:
    try:
        config = McpServerConfig.from_env()
    except McpConfigError as exc:
        print(f"agent-legion-mcp: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    create_mcp_server(config).run()
