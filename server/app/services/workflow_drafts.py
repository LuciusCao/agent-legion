"""Workflow draft YAML helpers (parse/validate) and publish-gate re-export.

The publish validation itself lives in ``workflow_draft_publish_gates``
(#432 split: this file hit its budget ceiling); re-exported here so the
existing import surface stays stable.
"""

from __future__ import annotations

import yaml

from server.app.services.workflow_draft_publish_gates import validate_workflow_for_publish
from server.app.workflows.definition import (
    WorkflowDefinition,
    WorkflowDefinitionError,
    workflow_definition_from_mapping,
)
from server.app.workflows.revision_format import definition_hash, serialize_definition

__all__ = [
    "validate_workflow_definition",
    "validate_workflow_for_publish",
    "workflow_definition_from_yaml_string",
    "workflow_draft_identity_hash",
]


def workflow_definition_from_yaml_string(raw_yaml: str) -> WorkflowDefinition:
    try:
        raw = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        raise WorkflowDefinitionError(f"Workflow definition is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise WorkflowDefinitionError("Workflow definition must be a mapping")
    return workflow_definition_from_mapping(raw)


def workflow_draft_identity_hash(raw_yaml: str) -> str | None:
    """#1143（方案 B）：草稿的语义归一化身份 hash——解析 → 归一化 → sha256。

    复用 #692 的 identity 先例（``serialize_definition`` = asdict 后
    sort_keys 紧凑 JSON，``definition_hash`` = sha256）：同一语义的两份
    YAML（agent 原始串 vs 画布按自身序列化规范的重排——键序、显式空列
    表、块式标量差异在解析归一化后消失）得到**相同** hash；这正是草稿
    卡「与编辑器不一致」提示按 hash 核对而非逐字节全等的依据。

    身份口径：hash 相同 = **loader 可见**语义相同——loader 丢弃的未知
    顶层键不参与身份（两侧一致的丢弃不构成语义分歧，评审 P3-2）。

    失败语义：不可解析（WorkflowDefinitionError / YAML / JSON 错误族，
    均为 ValueError）、含不可 JSON 化的 config 值（date 等对象，
    TypeError）、或深嵌套结构（千层 mapping 在解析/归一化链上抛
    RecursionError——RuntimeError 族，评审 P3-1）的草稿没有语义身份，
    返回 None——调用方按「无法核对身份」降级，绝不落 500。
    """
    try:
        definition = workflow_definition_from_yaml_string(raw_yaml)
        return definition_hash(serialize_definition(definition))
    except (RecursionError, TypeError, ValueError):
        # #204 broad-except audit: 失败语义是「数据态而非编程错误」——
        # 草稿文本是用户/agent 的不可信输入，draft store 允许暂存任意文本
        # （GET/PUT 回显、validate/compare 每次都计算身份），读路径必须
        # 把无身份降级为 None（调用方降级字符串比较）而不是 500。结果
        # 空间只有 None，无吞错静默——身份缺失在 stale hint 侧表现为
        # 保守提示；三类异常各自对应一种已声明的数据态（见 docstring）。
        return None


def validate_workflow_definition(
    raw_yaml: str,
) -> list[str]:
    try:
        workflow_definition_from_yaml_string(raw_yaml)
    except WorkflowDefinitionError as exc:
        return [str(exc)]
    return []
