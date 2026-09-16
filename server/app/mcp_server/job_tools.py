"""Job observation tools for the studio-agent MCP server (issue #329).

Registered onto the shared FastMCP instance from ``server.create_mcp_server``
(split out for the file-size budget, same as skill_tools/prompt_tools). All
six are read-only loopback tools and stay ``async def`` for the same
single-event-loop reason documented in ``server.py``.

There is deliberately no retry/rerun tool: agents only read and then quote the
``suggested_actions`` payloads back in their reply — the UI renders those as
human-confirmation cards and the host session executes on the regular job
routes (STUDIO-AGENT-001).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import quote, urlencode

from mcp.server.fastmcp import FastMCP

from server.app.mcp_server.config import McpServerConfig
from server.app.mcp_server.tool_client import ToolClient

ClientFactory = Callable[[], Awaitable[tuple[McpServerConfig, ToolClient]]]


def _ws(workspace_id: str) -> str:
    return f"/workspaces/{quote(workspace_id, safe='')}"


def _query(params: dict[str, object]) -> str:
    pairs = {key: value for key, value in params.items() if value is not None}
    return f"?{urlencode(pairs)}" if pairs else ""


def register_job_tools(
    mcp: FastMCP, client_factory: ClientFactory, *, session_bound: bool = True
) -> None:
    # #660: get_job_context is session-bound — a static external config
    # without a chat session never registers it (it could only answer
    # "unavailable"); the HTTP transport always passes session_bound=True.
    if session_bound:

        @mcp.tool(structured_output=False)
        async def get_job_context(job_id: str, node_key: str | None = None) -> str:
            """Session-bound diagnosis context: bound workspace, full job
            detail, focus node (node_key wins, else failed/running), other
            jobs' recent failures on it, suggested_actions for failed nodes.
            Call FIRST in a diagnosis session; the job must belong to the
            session's workspace (else 404). Read-only — never claim you
            executed a suggestion."""
            config, client = await client_factory()
            if config.session_id is None:
                return "get_job_context is unavailable: no chat session bound"
            path = f"/chat-sessions/{config.session_id}/job-context"
            path += _query({"job_id": job_id, "node_key": node_key})
            return await client.call("GET", path)

    @mcp.tool(structured_output=False)
    async def get_job_detail(workspace_id: str, job_id: str) -> str:
        """Read-only detail of one job: summary, per-node status/error with
        declared input/output names, run list (ids for get_node_logs),
        artifact names, suggested_actions for failed nodes."""
        _, client = await client_factory()
        path = f"{_ws(workspace_id)}/jobs/{quote(job_id, safe='')}"
        return await client.call("GET", path)

    @mcp.tool(structured_output=False)
    async def get_node_logs(
        workspace_id: str,
        job_id: str,
        node_key: str | None = None,
        run_id: int | None = None,
    ) -> str:
        """Sanitized execution log of one node run (paths/secrets redacted,
        tail-capped). run_id picks an exact run, node_key its latest; neither
        → the job's latest failed run."""
        _, client = await client_factory()
        path = f"{_ws(workspace_id)}/jobs/{quote(job_id, safe='')}/logs"
        path += _query({"node_key": node_key, "run_id": run_id})
        return await client.call("GET", path)

    @mcp.tool(structured_output=False)
    async def read_artifact(workspace_id: str, job_id: str, artifact_name: str) -> str:
        """Read one named job artifact as text (head-capped). Names come from
        get_job_detail — upstream inputs are the upstream node's output
        names."""
        _, client = await client_factory()
        path = f"{_ws(workspace_id)}/jobs/{quote(job_id, safe='')}/artifacts/{quote(artifact_name, safe='')}"
        return await client.call("GET", path)

    @mcp.tool(structured_output=False)
    async def list_jobs(workspace_id: str, status: str | None = None, limit: int = 20) -> str:
        """Recent jobs of the workspace (newest first) with status,
        error_summary, per-node summaries. status filters (e.g. "failed");
        limit caps at 100. For fleet questions and picking compare_jobs
        pairs."""
        _, client = await client_factory()
        path = f"{_ws(workspace_id)}/jobs" + _query({"status": status, "limit": limit})
        return await client.call("GET", path)

    @mcp.tool(structured_output=False)
    async def compare_jobs(workspace_id: str, job_id_a: str, job_id_b: str) -> str:
        """Diff two jobs of the same workspace (typical: A = failed, B = last
        success): per-node status/error side by side, newly-failed and
        recovered summaries."""
        _, client = await client_factory()
        path = f"{_ws(workspace_id)}/jobs/compare"
        path += _query({"job_id_a": job_id_a, "job_id_b": job_id_b})
        return await client.call("GET", path)
