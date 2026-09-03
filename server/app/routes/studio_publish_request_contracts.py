"""Contracts for agent-initiated workflow publish requests (#416).

Shared by the studio-agent tool surface (request/status) and the human-facing
Studio endpoints (pending read / confirm / cancel).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StudioPublishRequestRecord(BaseModel):
    """One row of the agent→human publish handshake.

    ``status`` lifecycle: pending → confirming (the human confirm claimed
    the row; its publish is in flight — cancel and new agent requests
    cannot touch it) → confirmed (``result_revision_id`` set only when the
    publish created a NEW revision — runtime-only config updates keep it
    null) | back to pending (the claimed publish was refused — the draft
    drifted; fixable and retryable) | rejected (human cancelled) | expired
    (past ``expires_at``; swept lazily on read). ``superseded``: displaced
    by a newer agent request or a manual publish.

    ``draft_hash``: sha256 of the server draft YAML at request time
    (#429 三轮 P1-3) — the confirm publishes exactly that draft or refuses.

    ``claimed_at``: stamped when the row moved to ``confirming``; null on
    every other state (#429 四轮 P1 — the stale-claim sweep's clock).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    chat_session_id: str | None = None
    status: str
    created_by: str
    result_revision_id: str | None = None
    draft_hash: str | None = None
    created_at: str
    expires_at: str
    resolved_at: str | None = None
    claimed_at: str | None = None


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
