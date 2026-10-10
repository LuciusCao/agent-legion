from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from server.app.agent_runtime.tool_catalog import VELITES_TOOL_CATALOG, default_tool_names
from server.app.config_schema import validate_config_schema

# #476：默认三件套来自 velites 工具目录的 default 档（与
# `velites tools list --json` 同源；值历史上就是 read/write/bash，这里只是
# 把单一事实来源从前端硬编码挪到 catalog 声明）。velites 是默认 runtime
# （#408），定义级默认不随所选 runtime 漂移——runtime 切换后的失效标记
# 与 dispatch 校验（#449）兜底。
DEFAULT_TOOLS: tuple[str, ...] = default_tool_names(VELITES_TOOL_CATALOG)

# #1167 评审 P3-1（#1173 codex 二轮根因收口）：agent_id（versioned_entities
# 的 entity_key）字符域契约的单一事实来源——与 skill key 段同款 ``_SEGMENT_RE``
# 形态：ASCII 起头 + 字母数字/点/下划线/连字符，天然排除 ``:``。executor_id
# 的 ``agent:<id>`` 形态里 ``:`` 是形态分隔符：命名为 ``code:x`` 的 agent 会
# 写出 ``agent:code:x`` 租约，命中 ``claim_node_limit`` 计数谓词的
# ``agent:code:%`` 前缀——agent 车道租约被计为 code 形态、消耗 code 节点
# 额度（#1167 症状回流）。
#
# agent_id 产生路径全集 × 校验点（#1173：校验下沉 service 写边界，一次封死
# 全部入口；契约层 pattern 只是提前的 4xx UX，不再是防线本身）：
#
# - 显式请求字段（POST /api/agent-definitions body ``agent_id``）
#   → AgentService.save_draft（经 create_agent_draft）
# - capability 派生（省略 agent_id → ``agent_id = capability``）
#   → create_agent_draft：派生值同过校验，非法派生报错并引导显式指定合法
#     agent_id（capability 字符域本身不收紧——路由/节点声明的语义键，牵连
#     面大，#1173 上轮论证）
# - PUT 路径参数（PUT /api/agent-definitions/{agent_id}/draft）
#   → AgentService.save_draft
# - Studio 端点（PUT /api/studio-agent/tools/.../agent-definitions/{agent_id}/draft
#   路径参数 + POST create 派生）
#   → AgentService.save_draft（经 StudioAgentToolsService → create_agent_draft）
# - copy 新键（POST /api/agent-definitions/{agent_id}/copy 的 ``new_agent_id``）
#   → AgentService.copy——唯一不经 save_draft 的写面：直接插新实体键 v1，
#     同域校验
#
# 存量兼容（选型「新建不允许、原位更新放行」）：无任何版本行的键 = 新建
# 实体，须过字符域；存有 pre-constraint 非常规键的实体原位更新/发布/回滚/
# 读取照常（grandfather，语义见 AgentService.save_draft）。非法键的首行
# 只能经 supported 写面之外的直写产生（存量数据/测试 helper），口径表
# 边界行如实记录（claim_node_limit docstring）。
AGENT_ID_RE = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
AGENT_ID_PATTERN = re.compile(AGENT_ID_RE)
# 错误文案用的字符域人话描述（service 层拼进报错，保持单源）。
AGENT_ID_CHARSET_RULE = "须以 ASCII 字母/数字开头，后续仅限字母/数字/./_/-"


def is_valid_agent_id(agent_id: str) -> bool:
    """Whether *agent_id* is a legal NEW agent entity key (charset above)."""
    return AGENT_ID_PATTERN.fullmatch(agent_id) is not None


def agent_id_charset_error(agent_id: str) -> str:
    """Error detail for an out-of-charset agent id（service 写边界 raise 4xx，
    文案单源于 ``AGENT_ID_CHARSET_RULE``——save_draft 新建实体与 copy 新键
    共用）。"""
    return (
        f"agent id {agent_id!r} 不在合法字符域：{AGENT_ID_CHARSET_RULE}"
        "（':' 会撞 executor_id 'agent:<id>' 的形态前缀，agent 车道租约会被"
        " 计成 code 节点额度，#1167）"
    )


class AgentDefinition(BaseModel):
    """Trusted, immutable definition of one logical Agent implementation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    capability: str = Field(min_length=1)
    runtime: Literal["pi", "velites"]
    # Legacy fallback for the node's skill binding (issue #76): "" means the
    # definition binds no skill and the workflow node must declare one.
    skill: str = ""
    tools: tuple[str, ...] = DEFAULT_TOOLS
    requires_labels: dict[str, str] = Field(default_factory=dict)
    config_schema: dict[str, Any] = Field(default_factory=dict)

    @field_validator("config_schema", mode="after")
    @classmethod
    def _validate_config_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        validate_config_schema(value)
        return value

    @field_validator("skill", mode="after")
    @classmethod
    def _reject_unsafe_skill_path(cls, value: str) -> str:
        if not value:
            return value
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("skill path must be relative and must not contain '..'")
        return value

    @field_validator("tools", mode="after")
    @classmethod
    def _reject_empty_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not tool for tool in value):
            raise ValueError("tool names must not be empty")
        return value

    def definition_hash(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
