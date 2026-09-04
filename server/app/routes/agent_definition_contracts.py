from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentDefinitionPayload(BaseModel):
    """Editable Agent definition fields (pure: no provider/model/thinking)."""

    capability: str = Field(min_length=1)
    runtime: Literal["pi", "velites"]
    # Optional legacy fallback for the node's skill binding (issue #76).
    skill: str = ""
    tools: list[str] = Field(default_factory=lambda: ["read", "write", "bash"])
    requires_labels: dict[str, str] = Field(default_factory=dict)
    config_schema: dict[str, Any] = Field(default_factory=dict)


class AgentCreateRequest(AgentDefinitionPayload):
    # #407：agent_id 可选——省略（或 null）时服务端按 capability 生成（一个
    # capability 一个主草稿）；显式传值保持旧客户端契约不变。
    agent_id: str | None = Field(default=None, min_length=1)


class AgentCopyRequest(BaseModel):
    new_agent_id: str = Field(min_length=1)


class AgentRollbackRequest(BaseModel):
    version: int = Field(ge=1)


class AgentVersionResponse(BaseModel):
    id: str
    agent_id: str
    version: int
    status: Literal["draft", "published", "archived"]
    definition: dict[str, Any]
    definition_hash: str
    created_by: str
    created_at: datetime
    published_at: datetime | None = None


class AgentVersionSummary(BaseModel):
    id: str
    agent_id: str
    version: int
    status: Literal["draft", "published", "archived"]
    definition_hash: str
    created_by: str
    created_at: datetime
    published_at: datetime | None = None


class AgentListItem(BaseModel):
    agent_id: str
    capability: str
    runtime: str
    skill: str
    version: int
    status: Literal["draft", "published", "archived"]
    has_draft: bool
    published_at: datetime | None = None


class AgentListResponse(BaseModel):
    agents: list[AgentListItem]


class AgentDetailResponse(BaseModel):
    agent_id: str
    latest: AgentVersionResponse | None = None
    published: AgentVersionResponse | None = None


class AgentVersionsResponse(BaseModel):
    versions: list[AgentVersionSummary]


class AgentArchiveResponse(BaseModel):
    archived: int
