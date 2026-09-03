"""Contracts for agent-initiated workflow publish requests (#416).

Shared by the studio-agent tool surface (request/status) and the human-facing
Studio endpoints (pending read / confirm / cancel).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StudioPublishRequestRecord(BaseModel):
    """One row of the agent→human publish handshake.

    ``status`` lifecycle: pending → superseded (a newer agent request or a
    manual publish displaced it) | confirmed (human confirmed;
    ``result_revision_id`` set only when the publish created a NEW revision
    — runtime-only config updates keep it null) | rejected (human
    cancelled) | expired (past ``expires_at``; swept lazily on read).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    chat_session_id: str | None = None
    status: str
    created_by: str
    result_revision_id: str | None = None
    created_at: str
    expires_at: str
    resolved_at: str | None = None


class StudioAgentPublishRequestResponse(BaseModel):
    """What the ``request_workflow_publish`` MCP tool returns: the parked
    request. Never a revision — the human confirms in Studio."""

    model_config = ConfigDict(extra="forbid")

    request: StudioPublishRequestRecord


class StudioAgentPublishRequestStatusResponse(BaseModel):
    """What the ``get_publish_request_status`` MCP tool returns."""

    model_config = ConfigDict(extra="forbid")

    request: StudioPublishRequestRecord


class StudioPublishRequestPendingResponse(BaseModel):
    """The workspace's live pending request (request=None when there is
    none); polled by the Studio frontend to pop the review dialog."""

    model_config = ConfigDict(extra="forbid")

    request: StudioPublishRequestRecord | None


class StudioPublishRequestResolveResponse(BaseModel):
    """Confirm/cancel outcome: the resolved request row."""

    model_config = ConfigDict(extra="forbid")

    request: StudioPublishRequestRecord
