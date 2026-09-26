"""Pydantic contracts for the workspace API intake token routes (#626)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateWorkspaceApiTokenRequest(BaseModel):
    # The token IS its workspace binding (the route's path scope); the label
    # is display-only, same budget as register-token labels.
    label: str = Field(default="", max_length=128)
    # Optional TTL in hours; None/absent = no expiry (revoke is the only
    # kill switch). Passed as hours so the wire contract stays integer-only.
    ttl_hours: int | None = Field(default=None, ge=1, le=24 * 365)


class WorkspaceApiTokenCreatedResponse(BaseModel):
    token_id: str
    # Plaintext "{token_id}.{secret}", returned exactly once at issuance.
    api_token: str
    workspace_id: str
    label: str


class WorkspaceApiTokenSummary(BaseModel):
    token_id: str
    workspace_id: str
    label: str
    created_at: str
    expires_at: str | None = None
    revoked: bool
    last_used_at: str | None = None


class WorkspaceApiTokensResponse(BaseModel):
    tokens: list[WorkspaceApiTokenSummary]


class WorkspaceApiTokenRevokeResponse(BaseModel):
    token_id: str
    revoked: bool
