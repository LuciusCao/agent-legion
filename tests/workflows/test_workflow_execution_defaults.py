"""Workflow 顶层 execution 默认：加载校验与向节点的合并（schema v63）。

workspace 级 Agent 默认（default_agent_*）退役后，顶层 ``execution`` 块是
workflow 作用域的默认来源：loader 把它合并进每个非 start 节点的 execution
（节点值优先），dispatch 读到的节点值即有效值，dispatch 本身不变。
"""

from __future__ import annotations

import pytest

from server.app.workflows.loader import (
    workflow_definition_from_dict,
    workflow_definition_from_mapping,
)
from server.app.workflows.schema import WorkflowDefinitionError

pytestmark = pytest.mark.no_db

_RAW = {
    "key": "wf",
    "label": "WF",
    "execution": {"provider": "top-provider", "model": "top-model", "thinking": "low"},
    "nodes": {
        "agent_node": {
            "capability": "review",
            "execution": {"provider": "node-provider", "prompt": "extra"},
        },
        "empty_node": {"capability": "generate"},
    },
}


def _load(raw: dict | None = None):
    return workflow_definition_from_mapping(raw if raw is not None else _RAW)


def test_top_level_execution_merges_into_nodes_node_wins() -> None:
    definition = _load()

    agent = definition.nodes["agent_node"]
    assert agent.execution.provider == "node-provider"  # 节点值优先
    assert agent.execution.model == "top-model"  # 空字段用顶层补
    assert agent.execution.thinking == "low"
    assert agent.execution.prompt == "extra"  # prompt 不被顶层触碰

    empty = definition.nodes["empty_node"]
    assert empty.execution.provider == "top-provider"
    assert empty.execution.model == "top-model"


def test_top_level_execution_does_not_touch_start_node() -> None:
    definition = _load()

    start = definition.start_node
    assert start is not None
    assert start.execution.provider == ""
    assert start.execution.model == ""


def test_definition_retains_top_level_execution() -> None:
    definition = _load()

    assert definition.execution.provider == "top-provider"
    assert definition.execution.model == "top-model"
    assert definition.execution.thinking == "low"


def test_no_top_level_execution_keeps_nodes_untouched() -> None:
    raw = {
        "key": "wf",
        "label": "WF",
        "nodes": {"n": {"capability": "x"}},
    }
    definition = _load(raw)

    assert definition.execution.provider == ""
    assert definition.nodes["n"].execution.provider == ""


def test_top_level_execution_must_be_a_mapping() -> None:
    raw = {**_RAW, "execution": "nope"}
    with pytest.raises(WorkflowDefinitionError, match="Workflow execution must be a mapping"):
        _load(raw)


def test_top_level_execution_fields_must_be_strings() -> None:
    raw = {**_RAW, "execution": {"provider": 42}}
    with pytest.raises(WorkflowDefinitionError, match="execution.provider must be a string"):
        _load(raw)


def test_top_level_execution_rejects_prompt() -> None:
    raw = {**_RAW, "execution": {"prompt": "global"}}
    with pytest.raises(WorkflowDefinitionError, match="execution.prompt is not allowed"):
        _load(raw)


def test_snapshot_round_trip_is_idempotent() -> None:
    # 真实快照路径：serialize_definition（asdict + json）→ from_dict。
    import json

    from server.app.services.workflow_revision_format import serialize_definition

    definition = _load()
    reloaded = workflow_definition_from_dict(json.loads(serialize_definition(definition)))

    assert reloaded.execution == definition.execution
    assert reloaded.nodes["agent_node"].execution == definition.nodes["agent_node"].execution
    assert reloaded.nodes["empty_node"].execution == definition.nodes["empty_node"].execution


def _dump(definition) -> str:
    import yaml

    from server.app.services.workflow_revision_format import definition_to_yaml

    return yaml.safe_load(definition_to_yaml(definition))


def test_echo_yaml_subtracts_baked_defaults_from_nodes() -> None:
    """回显（Studio 草稿与 chat agent 共用 definition_to_yaml）不能把 loader
    烘焙进节点的顶层默认再吐成节点级显式覆盖——否则用户之后改顶层默认
    再发布会被节点烘焙值静默压过。"""
    dumped = _dump(_load())

    # 顶层默认照常在 execution: 块（位于 nodes: 之前）。
    assert dumped["execution"] == {
        "provider": "top-provider",
        "model": "top-model",
        "thinking": "low",
    }
    assert list(dumped).index("execution") < list(dumped).index("nodes")
    # agent_node 只保留真实覆盖（provider 与顶层不同）与 prompt；与顶层一致
    # 的 model/thinking 不输出。
    assert dumped["nodes"]["agent_node"]["execution"] == {
        "provider": "node-provider",
        "prompt": "extra",
    }
    # empty_node 的全部 execution 值都来自顶层 → 不输出 execution 块。
    assert "execution" not in dumped["nodes"]["empty_node"]


def test_echo_yaml_round_trip_preserves_effective_execution() -> None:
    definition = _load()
    reloaded = workflow_definition_from_mapping(_dump(definition))

    assert reloaded.execution == definition.execution
    for key, node in definition.nodes.items():
        assert reloaded.nodes[key].execution == node.execution


def test_top_level_edit_after_echo_round_trip_takes_effect() -> None:
    """yaml → load → dump → 只改顶层 → load：顶层修改必须生效（回归：
    烘焙回显曾让节点显式值永久压过顶层修改）。"""
    dumped = _dump(_load())
    dumped["execution"]["model"] = "new-model"

    reloaded = workflow_definition_from_mapping(dumped)

    # 没有节点级覆盖的节点吃到新顶层值。
    assert reloaded.nodes["empty_node"].execution.model == "new-model"
    # 节点真实覆盖（provider）不受顶层改动影响；其继承来的 model 跟随顶层。
    assert reloaded.nodes["agent_node"].execution.provider == "node-provider"
    assert reloaded.nodes["agent_node"].execution.model == "new-model"
