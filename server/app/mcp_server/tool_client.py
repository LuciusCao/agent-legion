"""Authenticated HTTP client for the studio-agent tool surface.

One ``call`` per tool endpoint; non-2xx responses come back as
``HTTP <code>: <body>`` text and network errors as ``request failed: ...``
instead of raising, so a failed call never crashes the MCP session.
"""

from __future__ import annotations

from typing import Any

import requests

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
