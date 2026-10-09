"""The session-scoped agent-legion MCP entry spec for studio chat sessions.

Split from spawn.py (issue #1140 budget reclaim): build_mcp_server_spec
builds the HttpMcpServer injected into session/new, pointing the agent at
this backend's tool surface with the scoped run token and chat session id
riding along as HTTP headers.
"""

from __future__ import annotations

from acp.schema import HttpHeader, HttpMcpServer

from server.app.mcp_server.config import SESSION_ID_HEADER
from server.app.mcp_server.http_app import MCP_URL_PATH


def build_mcp_server_spec(*, token: str, api_base: str, session_id: str) -> HttpMcpServer:
    """The session-scoped agent-legion MCP entry injected into session/new.

    kimi ≥ 0.38 only accepts http/sse MCP servers over ACP, so the backend
    serves the tool surface itself (server.app.mcp_server.http_app) and the
    session points at that URL. The raw scoped token crosses only as an HTTP
    header inside the ACP session/new request — never persisted, never logged
    (STUDIO-AGENT-001). The chat session id rides along (SESSION_ID_HEADER)
    so the get_studio_context tool can resolve this session's live context.
    """
    return HttpMcpServer(
        type="http",
        name="agent-legion-studio",
        url=f"{api_base}{MCP_URL_PATH}",
        headers=[
            HttpHeader(name="Authorization", value=f"Bearer {token}"),
            HttpHeader(name=SESSION_ID_HEADER, value=session_id),
        ],
    )
