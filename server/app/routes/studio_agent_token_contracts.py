from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from server.app.services.studio_agent_tokens import DEFAULT_TTL_HOURS, MAX_TTL_HOURS


class StudioAgentTokenMintRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ttl_hours: int = Field(default=DEFAULT_TTL_HOURS, ge=1, le=MAX_TTL_HOURS)


class StudioAgentTokenMintResponse(BaseModel):
    """Mint result; the raw token is returned exactly once, here."""

    model_config = ConfigDict(extra="forbid")

    id: str
    token: str
    expires_at: str


class StudioAgentTokenEntry(BaseModel):
    """Management view of one token — never carries digest or plaintext."""

    model_config = ConfigDict(extra="forbid")

    id: str
    created_at: str
    expires_at: str
    revoked_at: str | None


class StudioAgentTokensResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tokens: list[StudioAgentTokenEntry]


class StudioAgentTokenRevokeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    revoked: bool
