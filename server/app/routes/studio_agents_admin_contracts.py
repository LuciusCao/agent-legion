"""Admin contracts for the Studio chat ACP agent registry (phase 3 chunk 4).

The registry is the only way a chat session gets its agent command line:
admins maintain {id, label, command, args[]} entries here and non-admin users
pick an agent by id — arbitrary command lines never cross the non-admin API
boundary (RCE guard). api_base tells the session-scoped MCP server where the
backend's own API lives; it also decides where the agent's MCP client sends
the session's scoped Bearer token, so it is validated as a plain absolute
http(s) URL at the write side (#158).
"""

from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StudioAgentRegistryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    label: str = Field(min_length=1)
    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)


class StudioAgentRegistryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_base: str = Field(min_length=1)
    agents: list[StudioAgentRegistryEntry] = Field(default_factory=list)

    @field_validator("api_base")
    @classmethod
    def _api_base_is_plain_http_url(cls, value: str) -> str:
        """api_base is the scoped-token egress target (#158): pin it to a
        plain absolute http(s) URL — no embedded credentials, query, or
        fragment — so a malformed entry fails the write instead of leaking
        tokens at session creation time."""
        parsed = urlsplit(value.strip())
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("api_base must be an absolute http(s) URL")
        if parsed.username or parsed.password:
            raise ValueError("api_base must not embed credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("api_base must not carry a query string or fragment")
        return value.strip().rstrip("/")

    @model_validator(mode="after")
    def _unique_agent_ids(self) -> StudioAgentRegistryDocument:
        ids = [agent.id for agent in self.agents]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate agent id in studio agent registry")
        return self


class StudioAgentRegistryResponse(StudioAgentRegistryDocument):
    """Stored document plus a PATH-probe result per agent id.

    ``availability`` is response-only (admins see which entries can actually
    launch on this host); it is never persisted and never accepted on PUT,
    so it lives here rather than on the shared document model.
    """

    availability: dict[str, bool] = Field(default_factory=dict)


class StudioAgentRegistryUpdate(StudioAgentRegistryDocument):
    pass
