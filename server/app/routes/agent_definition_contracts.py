from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from server.app.agent_catalog.definition import AGENT_ID_RE, DEFAULT_TOOLS

# #1167 评审 P3-1 / #1173 codex 二轮：``AGENT_ID_RE`` 的单一事实来源在
# ``agent_catalog.definition``（字符域语义、产生路径×校验点矩阵与存量兼容
# 选型见其注释）。权威校验在 service 写边界（``AgentService.save_draft``/
# ``copy``：新建实体即拒、存量非常规键原位更新放行）——契约层 pattern 只是
# 提前的 422 UX（省一次 DB 往返），不再是防线本身。


class AgentDefinitionPayload(BaseModel):
    """Editable Agent definition fields (pure: no provider/model/thinking)."""

    capability: str = Field(min_length=1)
    runtime: Literal["pi", "velites"]
    # Optional legacy fallback for the node's skill binding (issue #76).
    skill: str = ""
    # #476：默认值与 AgentDefinition 同源（catalog default 档）。
    tools: list[str] = Field(default_factory=lambda: list(DEFAULT_TOOLS))
    requires_labels: dict[str, str] = Field(default_factory=dict)
    config_schema: dict[str, Any] = Field(default_factory=dict)


class AgentCreateRequest(AgentDefinitionPayload):
    # #407：agent_id 可选——省略（或 null）时服务端按 capability 生成（一个
    # capability 一个主草稿）；显式传值保持旧客户端契约不变。#1167 评审
    # P3-1：显式值须过字符域（防 ``:`` 撞 executor_id 形态前缀）——契约层
    # 422 只是提前 UX；显式、派生（create_agent_draft 引导报错）、PUT 路径
    # 参数、Studio 端点与 copy 全部由 service 写边界单点封死（#1173 codex
    # 二轮，矩阵见 agent_catalog.definition）。
    agent_id: str | None = Field(default=None, min_length=1, pattern=AGENT_ID_RE)


class AgentCopyRequest(BaseModel):
    new_agent_id: str = Field(min_length=1, pattern=AGENT_ID_RE)


class AgentRollbackRequest(BaseModel):
    version: int = Field(ge=1)


class AgentPublishRequest(BaseModel):
    """#692 codex P1: the caller's asserted draft hash — verified atomically
    inside the publish transaction; mismatch raises 409 with zero publish
    side effects. #841: required — a missing body or field is 422 (the
    hash-less legacy semantics are retired; read the draft's
    ``definition_hash`` from the save/detail response first)."""

    expected_hash: str = Field(min_length=1)


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


class AgentArchiveResponse(BaseModel):
    archived: int
