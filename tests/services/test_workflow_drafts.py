"""#1143（方案 B）：workflow 草稿语义身份 hash（解析 → 归一化 → sha256）。

修复的本质属性在这里钉住：同一语义的两份 YAML（agent 原始串 vs 画布按
自身序列化规范的重排——键序、显式空列表、块式标量差异）必须产生相同
hash，语义变化必须产生不同 hash——否则草稿卡的一致性判定仍会被字节
差异误报，或对真实分歧漏报。
"""

from __future__ import annotations

import hashlib

from server.app.services.workflow_drafts import (
    workflow_definition_from_yaml_string,
    workflow_draft_identity_hash,
)
from server.app.services.workflow_revision_format import definition_to_yaml
from server.app.workflows.revision_format import serialize_definition

_AGENT_YAML = """
schema_version: 2
key: demo
label: Demo
nodes:
  review:
    type: agent
    capability: review_keywords
    inputs: []
    outputs: []
    execution:
      runtime: velites
      prompt: |
        第一行
        第二行
    config:
      count: 3
edges: []
intake:
  modes:
    manual:
      label: Manual
      input_field: text
"""

# 手工等价重排：顶层/节点键序打乱、inputs/outputs 省略（默认空）、行内
# mapping、块式标量——语义与 _AGENT_YAML 完全相同。
_REORDERED_YAML = """
intake:
  modes:
    manual: {label: Manual, input_field: text}
nodes:
  review:
    config: {count: 3}
    execution:
      prompt: |
        第一行
        第二行
      runtime: velites
    capability: review_keywords
    type: agent
edges: []
label: Demo
key: demo
schema_version: 2
"""


def test_identity_hash_matches_the_serialize_definition_pipeline() -> None:
    definition = workflow_definition_from_yaml_string(_AGENT_YAML)
    expected = hashlib.sha256(serialize_definition(definition).encode("utf-8")).hexdigest()
    assert workflow_draft_identity_hash(_AGENT_YAML) == expected


def test_agent_yaml_and_canvas_reserialization_hash_identically() -> None:
    """#1143 修复核心：画布重排（definition_to_yaml 规范序列化）后字节必然
    不同，但语义相同——hash 必须相同，草稿卡才不会误报不一致。"""
    canvas_yaml = definition_to_yaml(workflow_definition_from_yaml_string(_AGENT_YAML))
    assert canvas_yaml != _AGENT_YAML.strip()
    assert workflow_draft_identity_hash(_AGENT_YAML) == workflow_draft_identity_hash(canvas_yaml)


def test_hand_reordered_equivalent_yaml_hashes_identically() -> None:
    assert workflow_draft_identity_hash(_AGENT_YAML) == workflow_draft_identity_hash(
        _REORDERED_YAML
    )


def test_semantic_change_produces_a_different_hash() -> None:
    mutated = _AGENT_YAML.replace("count: 3", "count: 4")
    assert workflow_draft_identity_hash(mutated) != workflow_draft_identity_hash(_AGENT_YAML)
    relabeled = _AGENT_YAML.replace("label: Demo", "label: Other")
    assert workflow_draft_identity_hash(relabeled) != workflow_draft_identity_hash(_AGENT_YAML)


def test_unparseable_drafts_have_no_identity() -> None:
    """不可解析/非 mapping 的草稿返回 None（调用方按「无法核对身份」降级），
    绝不抛错——draft store 允许暂存未通过校验的 YAML。"""
    assert workflow_draft_identity_hash("key: [unclosed") is None
    assert workflow_draft_identity_hash("- a\n- b\n") is None
    assert workflow_draft_identity_hash("") is None
