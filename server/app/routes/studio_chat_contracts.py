"""API contracts for the Studio chat surface (phase 3 chunk 4, ACP client)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SessionStatus = Literal["starting", "idle", "running", "awaiting_permission", "closed", "error"]
MessageKind = Literal["text", "tool_call", "plan", "permission", "status"]
MessageRole = Literal["user", "agent", "system"]
McpStatus = Literal["unknown", "verified", "unverified"]


class StudioChatAgentOption(BaseModel):
    """Picker view of a registry agent: never exposes command/args."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str


class StudioChatAgentsResponse(BaseModel):
    agents: list[StudioChatAgentOption]


class StudioChatSessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    title: str = ""


class StudioChatSessionRecord(BaseModel):
    id: str
    workspace_id: str
    user_id: str
    agent_id: str
    title: str
    status: SessionStatus
    acp_session_id: str | None
    capability_snapshot: dict[str, Any]
    allow_all_permissions: bool
    mcp_status: McpStatus
    error_detail: str
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None


class StudioChatSessionResponse(BaseModel):
    session: StudioChatSessionRecord


class StudioChatSessionsResponse(BaseModel):
    sessions: list[StudioChatSessionRecord]


class StudioChatMessageCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)


class StudioChatMessageRecord(BaseModel):
    id: str
    session_id: str
    kind: MessageKind
    role: MessageRole
    content: dict[str, Any]
    seq: int
    created_at: datetime


class StudioChatMessageResponse(BaseModel):
    message: StudioChatMessageRecord


class StudioChatMessagesResponse(BaseModel):
    messages: list[StudioChatMessageRecord]


class StudioChatAllowAllRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class StudioChatPermissionAnswerRequest(BaseModel):
    """Human answer to a forwarded permission prompt: pick an option or deny."""

    model_config = ConfigDict(extra="forbid")

    option_id: str | None = None
    deny: bool = False


class StudioChatPermissionAnswerResponse(BaseModel):
    resolved: str
