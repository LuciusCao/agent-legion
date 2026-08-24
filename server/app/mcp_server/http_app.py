"""In-app streamable-HTTP MCP endpoint for Studio chat sessions.

kimi ≥ 0.38 accepts only ``type: "http" | "sse"`` MCP servers in ACP
``session/new`` — a stdio entry without ``type`` is rejected outright, so the
per-session stdio injection no longer works. The backend therefore serves the
studio-agent tool surface itself at ``/api/studio-agent/mcp`` and injects
that URL (plus the scoped token and chat session id as headers) into each
chat session's ACP handshake.

The endpoint lives outside the FastAPI routers (an ASGI mount), so it does
not inherit the route-layer auth dependencies; the ``_ScopedTokenAuthApp``
wrapper is its only guard and mirrors ``require_studio_agent_scope``
(STUDIO-AGENT-001): no valid studio-agent scoped Bearer token, no MCP
handshake. Tool calls themselves still go through ``ToolClient`` loopback to
``/api/studio-agent/tools/*``, where the route layer re-validates the same
token and enforces the workspace binding.
"""

from __future__ import annotations

import json

import anyio
from mcp.server.fastmcp import FastMCP
from starlette.types import ASGIApp, Receive, Scope, Send

from server.app.auth.scoped_tokens import STUDIO_AGENT_SCOPE, authenticate_scoped_token
from server.app.jobs import JobQueries
from server.app.mcp_server.config import McpServerConfig
from server.app.mcp_server.server import create_mcp_server

# Mounted at MCP_MOUNT_PATH inside the FastAPI app; the FastMCP route keeps
# its default inner path, so agents connect to MCP_URL_PATH.
MCP_MOUNT_PATH = "/api/studio-agent"
MCP_URL_PATH = f"{MCP_MOUNT_PATH}/mcp"


class _ScopedTokenAuthApp:
    """ASGI guard: only studio-agent scoped Bearer tokens reach the MCP app."""

    def __init__(self, app: ASGIApp, job_db: JobQueries) -> None:
        self._app = app
        self._db = job_db

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = {str(k, "latin-1"): str(v, "latin-1") for k, v in scope["headers"]}
        scheme, _, raw_token = headers.get("authorization", "").partition(" ")
        token = raw_token.strip()
        # authenticate_scoped_token is a blocking DB query; keep it off the
        # event loop so it cannot stall every request on this worker.
        user = (
            await anyio.to_thread.run_sync(authenticate_scoped_token, self._db, token)
            if scheme.lower() == "bearer" and token
            else None
        )
        if user is None or user.get("actor_scope") != STUDIO_AGENT_SCOPE:
            body = json.dumps({"detail": "Studio agent scoped token required"}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self._app(scope, receive, send)


def create_studio_mcp_http_app(job_db: JobQueries, api_base: str) -> tuple[FastMCP, ASGIApp]:
    """Build the mountable MCP app. The returned FastMCP instance must have
    its session manager run inside the host app's lifespan (mounted sub-app
    lifespans do not propagate). The session manager is single-use, so a
    lifespan re-entry must rebuild the pair — mount a StudioMcpRelay and swap
    the app in on every entry instead of mounting this app directly."""

    def resolve_config() -> McpServerConfig:
        request = mcp.get_context().request_context.request
        if request is None:
            raise RuntimeError("studio MCP HTTP transport has no active request")
        return McpServerConfig.from_headers(request.headers, api_base=api_base)

    mcp = create_mcp_server(resolve_config)
    return mcp, _ScopedTokenAuthApp(mcp.streamable_http_app(), job_db)


class StudioMcpRelay:
    """Mounted ASGI relay that forwards to the lifespan's current MCP app.

    StreamableHTTPSessionManager.run() can only run once per instance, and an
    app's lifespan may be entered more than once (tests re-enter one app
    across TestClient sessions): each entry builds a fresh MCP app and swaps
    it in here, so the Mount target stays stable.
    """

    def __init__(self) -> None:
        self._app: ASGIApp | None = None

    def set(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        app = self._app
        if app is None:
            if scope["type"] == "http":
                body = json.dumps({"detail": "MCP endpoint not ready"}).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 503,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send({"type": "http.response.body", "body": body})
            return
        await app(scope, receive, send)


__all__ = ["MCP_MOUNT_PATH", "MCP_URL_PATH", "StudioMcpRelay", "create_studio_mcp_http_app"]
