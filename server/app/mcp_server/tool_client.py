"""Authenticated HTTP client for the studio-agent tool surface.

One ``call`` per tool endpoint; non-2xx responses come back as
``HTTP <code>: <body>`` text and network errors as ``request failed: ...``
instead of raising, so a failed call never crashes the MCP session.

The client is fully async (httpx). The in-app HTTP transport executes tools
on the uvicorn event loop, and the loopback target's sync route handlers run
on the SAME shared anyio worker pool — offloading the loopback call to that
pool (``anyio.to_thread``) would let concurrent tool calls occupy every
worker thread while the handlers they wait on can never start: a thread-pool
deadlock under concurrency, only unwound by the 30s timeout.
"""

from __future__ import annotations

from typing import Any

import httpx

from server.app.mcp_server.config import McpServerConfig

REQUEST_TIMEOUT_SECONDS = 30
_TOOLS_PATH = "/api/studio-agent/tools"


class ToolClient:
    """Bearer-token client forwarding tool calls to a running backend."""

    def __init__(self, config: McpServerConfig):
        self._api_base = config.api_base
        self._headers = {
            "Authorization": f"Bearer {config.token}",
            "Content-Type": "application/json",
        }

    async def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> str:
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as http:
                response = await http.request(
                    method,
                    f"{self._api_base}{_TOOLS_PATH}{path}",
                    json=body,
                    headers=self._headers,
                )
        except httpx.HTTPError as exc:
            return f"request failed: {exc}"
        if 200 <= response.status_code < 300:
            return response.text
        return f"HTTP {response.status_code}: {response.text}"
