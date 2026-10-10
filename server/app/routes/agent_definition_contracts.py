from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from server.app.agent_catalog.definition import DEFAULT_TOOLS

# #1167 评审 P3-1：agent_id 的字符域契约（与 skill key 段同款
# ``_SEGMENT_RE``——ASCII 起头 + 字母数字/点/下划线/连字符，天然排除
# ``:``）。executor_id 的 ``agent:<id>`` 形态里 ``:`` 是形态分隔符：
# 命名为 ``code:x`` 的 agent 会写出 ``agent:code:x`` 租约，命中
# ``claim_node_limit`` 计数谓词的 ``agent:code:%`` 前缀——agent 车道
# 租约被计为 code 形态、消耗 code 节点额度（#1167 症状回流）。约束只
# 管创建/复制入口（新值）；存量已存在的非常规命名不动（口径表边界行
# 如实记录，见 claim_node_limit docstring）。
AGENT_ID_RE = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


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
    # P3-1：显式值须过字符域（防 ``:`` 撞 executor_id 形态前缀）；派生
    # 路径（capability）的字符域同参 P3-1 的口径表边界行说明。
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
