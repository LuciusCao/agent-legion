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
