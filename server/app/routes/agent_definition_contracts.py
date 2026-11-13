from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


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
    # #906: the latest row can be a draft whose capability differs from the
    # published version that actually routes; null = never published.
    published_capability: str | None = None
    published_version: int | None = None


class AgentListResponse(BaseModel):
    agents: list[AgentListItem]


class AgentDetailResponse(BaseModel):
    agent_id: str
    latest: AgentVersionResponse | None = None
    published: AgentVersionResponse | None = None


class AgentVersionsResponse(BaseModel):
    versions: list[AgentVersionSummary]
