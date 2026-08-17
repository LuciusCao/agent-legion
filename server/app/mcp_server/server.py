"""Thin MCP (stdio) wrapper over the studio-agent tool surface.

Each tool forwards to one ``/api/studio-agent/tools/*`` endpoint of a running
Agent Legion backend, authenticated with a studio-agent scoped token
(STUDIO-AGENT-001). Tools return the response body as text; non-2xx responses
come back as ``HTTP <code>: <body>`` text instead of raising, so a failed
call never crashes the MCP session.
"""

from __future__ import annotations

import sys
from typing import Any

import requests
from mcp.server.fastmcp import FastMCP

from server.app.mcp_server.config import McpConfigError, McpServerConfig

REQUEST_TIMEOUT_SECONDS = 30
_TOOLS_PATH = "/api/studio-agent/tools"


class _ToolClient:
    """Authenticated HTTP client for the studio-agent tool surface."""

    def __init__(self, config: McpServerConfig):
        self._api_base = config.api_base
        self._headers = {
            "Authorization": f"Bearer {config.token}",
            "Content-Type": "application/json",
        }

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> str:
        try:
            response = requests.request(
                method,
                f"{self._api_base}{_TOOLS_PATH}{path}",
                json=body,
                headers=self._headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            return f"request failed: {exc}"
        if 200 <= response.status_code < 300:
            return response.text
        return f"HTTP {response.status_code}: {response.text}"


def create_mcp_server(config: McpServerConfig) -> FastMCP:
    """Build the FastMCP server exposing the studio-agent tools."""
    mcp = FastMCP("agent-legion-studio")
    client = _ToolClient(config)

    @mcp.tool()
    def get_studio_context() -> str:
        """Current Studio session context: the bound workspace, its active
        workflow structure (nodes and capabilities), and the node the human
        currently has selected in Studio (live value on every call). Takes no
        workspace_id — the session binding decides which workspace you operate
        on. Call this first when you need workspace or selection context."""
        if config.session_id is None:
            return "get_studio_context is unavailable: no chat session bound"
        return client.call("GET", f"/chat-sessions/{config.session_id}/context")

    @mcp.tool()
    def list_workflows() -> str:
        """List all workflows registered in the Agent Legion catalog (key,
        label, status). Start here to discover which workflow keys exist."""
        return client.call("GET", "/workflows")

    @mcp.tool()
    def get_active_workflow(workspace_id: str) -> str:
        """Get the active workflow revision of a workspace, including the full
        definition YAML. Read this before drafting changes so the draft builds
        on what is actually live. When the workspace has no published workflow
        yet, the result is a structured empty state ({"state": "empty",
        "workflow_key": ...}) instead of an error — treat it as the signal to
        start the from-scratch authoring flow (see get_authoring_guide)."""
        return client.call("GET", f"/workspaces/{workspace_id}/workflow/active")

    @mcp.tool()
    def validate_workflow(workspace_id: str, definition_yaml: str) -> str:
        """Validate a workflow definition YAML draft against the publish
        validation set. Persists nothing. Always validate a draft before
        asking the human to review or apply it."""
        return client.call(
            "POST",
            f"/workspaces/{workspace_id}/workflow/validate",
            {"definition_yaml": definition_yaml},
        )

    @mcp.tool()
    def compare_workflow(workspace_id: str, definition_yaml: str) -> str:
        """Diff a workflow definition YAML draft against the workspace's active
        revision: per-node changes, risk summary, whether it would create a new
        revision. When the workflow was never published (no baseline), the
        result is a full-draft preview instead: every node/edge/intake field
        shows as added and base_revision is null. Persists nothing."""
        return client.call(
            "POST",
            f"/workspaces/{workspace_id}/workflow/compare",
            {"definition_yaml": definition_yaml},
        )

    @mcp.tool()
    def save_node_code_draft(
        workspace_id: str,
        workflow_key: str,
        node_key: str,
        code: str,
        change_note: str = "",
        expected_capability: str | None = None,
    ) -> str:
        """Save a draft of a code node's Python source (the module must expose
        ``run(job, job_dir, runtime)``). This only creates a draft version —
        a human reviews and publishes it in Studio; the draft never runs in
        production jobs by itself. Pass expected_capability to declare the
        capability you believe the node binds: an existing node whose
        capability differs is rejected with a clear error; a node that does
        not exist yet (new node, or no published revision at all) is only
        accepted when expected_capability is set, creating a skeleton draft
        ahead of the workflow draft that introduces the node."""
        body: dict[str, Any] = {"code": code, "change_note": change_note or None}
        if expected_capability is not None:
            body["expected_capability"] = expected_capability
        return client.call(
            "PUT",
            f"/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code/draft",
            body,
        )

    @mcp.tool()
    def get_node_code(workspace_id: str, workflow_key: str, node_key: str) -> str:
        """Read the current code state of a workflow code node: builtin source,
        published custom code, and any pending draft."""
        return client.call(
            "GET",
            f"/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code",
        )

    @mcp.tool()
    def save_agent_definition_draft(
        agent_id: str,
        capability: str,
        runtime: str,
        skill: str,
        tools: list[str] | None = None,
    ) -> str:
        """Save a draft Agent definition binding a capability to a runtime and
        skill. runtime is one of: pi, openclaw, velites. Draft only — a human
        publishes the definition in Studio before any job can use it."""
        return client.call(
            "PUT",
            f"/agent-definitions/{agent_id}/draft",
            {
                "capability": capability,
                "runtime": runtime,
                "skill": skill,
                "tools": tools or ["read", "write", "bash"],
            },
        )

    @mcp.tool()
    def register_workflow(workflow_key: str, label: str, description: str = "") -> str:
        """Register a new workflow key in the catalog. Registration alone has
        no scheduling effect: jobs only flow once a human publishes the first
        workflow revision."""
        return client.call(
            "POST",
            "/workflows/register",
            {"key": workflow_key, "label": label, "description": description},
        )

    return mcp


def main() -> None:
    try:
        config = McpServerConfig.from_env()
    except McpConfigError as exc:
        print(f"agent-legion-mcp: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    create_mcp_server(config).run()
