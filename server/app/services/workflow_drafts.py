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
    卡「与编辑器不一致」提示按 hash 核对而非逐字节全等的依据。失败的
    语义：不可解析（WorkflowDefinitionError / YAML / JSON 错误族，均为
    ValueError）或含不可 JSON 化的 config 值（date 等对象，TypeError）
    的草稿没有语义身份，返回 None——调用方按「无法核对身份」降级，
    绝不落 500。
    """
    try:
        definition = workflow_definition_from_yaml_string(raw_yaml)
        return definition_hash(serialize_definition(definition))
    except (TypeError, ValueError):
        return None


def validate_workflow_definition(
    raw_yaml: str,
) -> list[str]:
    try:
        workflow_definition_from_yaml_string(raw_yaml)
    except WorkflowDefinitionError as exc:
        return [str(exc)]
    return []
