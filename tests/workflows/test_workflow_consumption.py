"""artifact 消费关系索引（workflow_consumption）的纯逻辑测试（issue #759）。

钉住三条消费渠道（node.inputs / RMW / edge.condition.artifact）共用一张
索引与一张合并邻接表——升级保护与 hydration 都以它为准，分支条件产物
的生产者与边不相邻时也必须传播（否则重跑/升级留下旧条件字节，分支
评估静默走错分支）。
"""

from __future__ import annotations

import pytest

from server.app.workflows.schema import (
    WorkflowCondition,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowIntake,
    WorkflowNode,
)
from server.app.workflows.workflow_consumption import (
    artifact_consumption_index,
    consumer_edges,
    dependency_children,
    dependency_downstream,
)

pytestmark = pytest.mark.no_db


def _node(key: str, *, inputs: list[str] | None = None, outputs: list[str] | None = None):
    return WorkflowNode(
        key=key, label=key, capability=key, inputs=inputs or [], outputs=outputs or []
    )


def _definition(nodes: dict[str, WorkflowNode], edges: list[WorkflowEdge]) -> WorkflowDefinition:
    return WorkflowDefinition(key="t", label="t", intake=WorkflowIntake(), nodes=nodes, edges=edges)


def test_index_covers_node_inputs_and_external_names() -> None:
    definition = _definition(
        {
            "a": _node("a", outputs=["x"]),
            "b": _node("b", inputs=["x", "ext_in"]),
        },
        [WorkflowEdge(source="a", target="b")],
    )
    index = artifact_consumption_index(definition)
    assert index == {"x": frozenset({"b"}), "ext_in": frozenset({"b"})}


def test_index_covers_branch_condition_artifacts() -> None:
    """edge.condition.artifact 的 target 是该名的消费者。"""
    definition = _definition(
        {"a": _node("a"), "b": _node("b")},
        [
            WorkflowEdge(
                source="a",
                target="b",
                condition=WorkflowCondition(artifact="verdict.json", path="$.ok", equals=True),
            )
        ],
    )
    index = artifact_consumption_index(definition)
    assert index == {"verdict.json": frozenset({"b"})}


def test_rmw_name_is_not_a_self_edge_but_propagates_to_others() -> None:
    definition = _definition(
        {
            "p": _node("p", inputs=["shared"], outputs=["shared"]),
            "q": _node("q", inputs=["shared"]),
        },
        [],
    )
    edges = consumer_edges(definition)
    assert "p" not in edges["p"]
    assert edges["p"] == ["q"]


def test_condition_artifact_producer_not_adjacent_to_edge_propagates() -> None:
    """codex P1 正解：条件产物由与边不相邻的节点生产时，producer→target
    隐式边是唯一的传播通道——重跑 producer 必须带走 gated target。"""
    definition = _definition(
        {
            "scorer": _node("scorer", outputs=["verdict.json"]),
            "entry": _node("entry"),
            "gated": _node("gated"),
        },
        [
            WorkflowEdge(source="entry", target="gated"),
            # 条件挂在 entry→gated 上，但 verdict.json 由 scorer 生产——
            # scorer 与这条边在显式图上无连接。
            WorkflowEdge(
                source="entry",
                target="gated",
                condition=WorkflowCondition(artifact="verdict.json", path="$.ok", equals=True),
            ),
        ],
    )
    assert dependency_downstream(definition, "scorer") == ["gated"]


def test_condition_artifact_self_producer_excluded() -> None:
    """target 自己生产条件产物（退化形态）不构成自边。"""
    definition = _definition(
        {"a": _node("a"), "b": _node("b", outputs=["verdict.json"])},
        [
            WorkflowEdge(
                source="a",
                target="b",
                condition=WorkflowCondition(artifact="verdict.json", path="$.ok", equals=True),
            )
        ],
    )
    children = dependency_children(definition)
    assert children["b"] == []
    assert children["a"] == ["b"]
<<<<<<< HEAD


def test_skip_names_still_filters_consumption_edges() -> None:
    definition = _definition(
        {
            "p": _node("p", outputs=["x"]),
            "q": _node("q", inputs=["x"]),
        },
        [],
    )
    assert consumer_edges(definition, skip_names=["x"])["p"] == []
    assert consumer_edges(definition)["p"] == ["q"]


def test_dropped_artifact_names_keep_condition_consumed_seeds() -> None:
    """#775 对抗复审 P1：clean upgrade 的死名判定走统一索引——旧产出仅被
    新定义的分支条件消费（不再产出、不在任何节点 inputs）时是种子不是垃
    圾：删掉它会让在途 job 的分支评估永远读不到条件文件。对照组：彻底
    无人消费的名字仍判死。"""
    from server.app.workflows.revision_diff import dropped_artifact_names

    old = _definition(
        {
            "entry": _node("entry"),
            "scorer": _node("scorer", outputs=["verdict.json"]),
            "extra": _node("extra", outputs=["stale.json"]),
        },
        [WorkflowEdge(source="entry", target="scorer")],
    )
    new = _definition(
        {"entry": _node("entry"), "gated": _node("gated")},
        [
            WorkflowEdge(source="entry", target="gated"),
            WorkflowEdge(
                source="entry",
                target="gated",
                condition=WorkflowCondition(artifact="verdict.json", path="$.ok", equals=True),
            ),
        ],
    )

    assert dropped_artifact_names(new, old) == {"stale.json"}  # verdict.json 保留
=======
>>>>>>> 3f038f6d7 (feat(jobs)：workflow 升级 inherit 模式全量——revision diff/实现身份/保护计划/cleanup + 发布锁域 #645 #759)
