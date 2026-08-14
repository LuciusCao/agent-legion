"""Environment configuration for the studio-agent MCP server.

Two variables, following the project's ``AGENT_LEGION_*`` naming:

- ``AGENT_LEGION_MCP_API_BASE`` — base URL of the Agent Legion backend
  (default ``http://127.0.0.1:8000``).
- ``AGENT_LEGION_STUDIO_AGENT_TOKEN`` — scoped token minted via
  ``POST /api/studio-agent-tokens``. Required; the server fails fast at
  startup when it is missing so a misconfigured MCP entry surfaces
  immediately instead of on first tool call.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

API_BASE_ENV = "AGENT_LEGION_MCP_API_BASE"
TOKEN_ENV = "AGENT_LEGION_STUDIO_AGENT_TOKEN"
DEFAULT_API_BASE = "http://127.0.0.1:8000"


class McpConfigError(RuntimeError):
    """Raised when the MCP server environment is incomplete."""


@dataclass(frozen=True)
class McpServerConfig:
    api_base: str
    token: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> McpServerConfig:
        env = os.environ if environ is None else environ
        token = env.get(TOKEN_ENV, "").strip()
        if not token:
            raise McpConfigError(
                f"{TOKEN_ENV} is not set; mint one with POST /api/studio-agent-tokens"
            )
        api_base = env.get(API_BASE_ENV, "").strip() or DEFAULT_API_BASE
        return cls(api_base=api_base.rstrip("/"), token=token)
