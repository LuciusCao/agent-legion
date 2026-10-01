"""Per-node upgrade diff 哈希的纯函数测试（issue #645 inherit 模式）。

全部不触库：构造 WorkflowDefinition 对，验证哈希稳定性与语义边界
（展示字段不触发、config 变化触发、上游传播、skill:latest / 分片 /
审批门排除、上游重命名坍缩）。702 传播闭包重构后，
``compute_node_hashes``（upstream 集哈希 + 拓扑序链式传播）已删除，
原「per-node 哈希传播」用例的断言迁移到
``compute_inherit_reset_nodes``（wrapper：种子 → 闭包）上——传播语义
不变（下游闭包），观测面从哈希值换成重置集。
"""

from __future__ import annotations

from dataclasses import replace

from server.app.services.job_workflow_upgrade_diff import (
    compute_inherit_reset_nodes,
    node_definition_hash,
    node_is_inherit_excluded,
)
from server.app.workflows.schema import (
    WorkflowCondition,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowIntake,
    WorkflowNode,
    WorkflowNodeSkill,
    WorkflowShardSpec,
)


def _node(key: str, **overrides) -> WorkflowNode:
    defaults = dict(key=key, label=key, capability=f"cap_{key}")
    defaults.update(overrides)
    return WorkflowNode(**defaults)


def _definition(nodes: dict, edges: list | None = None) -> WorkflowDefinition:
    return WorkflowDefinition(
        key="wf",
        label="Wf",
        intake=WorkflowIntake(),
        nodes=nodes,
        edges=edges or [],
    )


def _chain_definition() -> WorkflowDefinition:
    """a → b → c 三级链。"""
    return _definition(
        {
            "a": _node("a"),
            "b": _node("b", after=["a"]),
            "c": _node("c", after=["b"]),
        },
        [WorkflowEdge(source="a", target="b"), WorkflowEdge(source="b", target="c")],
    )


def test_label_change_does_not_change_definition_hash():
    node = _node("a", label="Original")
    relabeled = replace(node, label="Renamed")

    assert node_definition_hash(relabeled) == node_definition_hash(node)


def test_capability_and_execution_changes_do_change_definition_hash():
    node = _node("a")
    assert node_definition_hash(replace(node, capability="other")) != node_definition_hash(node)
    assert node_definition_hash(replace(node, inputs=["in.json"])) != node_definition_hash(node)
    assert node_definition_hash(replace(node, outputs=["out.json"])) != node_definition_hash(node)


def test_node_hashes_are_stable_for_identical_definitions():
    """局部哈希稳定性（原 compute_node_hashes 删除，断言迁移到 wrapper）：

    同一定义两次计算零种子 → 全继承（reset 空）；种子收集是纯函数，
    相同输入产出相同种子集。
    """
    one = compute_inherit_reset_nodes(_chain_definition(), None, _chain_definition(), None)
    two = compute_inherit_reset_nodes(_chain_definition(), None, _chain_definition(), None)

    assert one == two == set()


def test_frozen_config_section_change_propagates_downstream():
    definition = _chain_definition()
    # a 的 config 段变化：a 自身变；b 是 a 的下游、c 是 b 的下游——闭包
    # 传播（原哈希链语义的 wrapper 等价面）让整个下游一起重跑。
    reset = compute_inherit_reset_nodes(
        definition, '{"a": {"k": "v1"}}', definition, '{"a": {"k": "v2"}}'
    )

    assert reset == {"a", "b", "c"}


def test_frozen_config_section_of_other_node_does_not_leak():
    definition = _chain_definition()
    # c 的 config 段变化：a/b 不受影响（无上游反向传播）。
    reset = compute_inherit_reset_nodes(
        definition, '{"c": {"k": "v1"}}', definition, '{"c": {"k": "v2"}}'
    )

    assert reset == {"c"}


def test_upstream_change_propagates_downstream_through_config_chain():
    definition = _chain_definition()
    # 只有 a 的 config 变：b/c 的定义与 config 未变，但 a 是种子、闭包
    # 把 b/c（下游）一并重置——下游传播。
    reset = compute_inherit_reset_nodes(
        definition, '{"a": {"k": "v1"}}', definition, '{"a": {"k": "v2"}}'
    )

    assert reset == {"a", "b", "c"}


def test_compute_inherit_reset_nodes_only_resets_changed_subgraph():
    old_def = _chain_definition()
    # b 的 capability 变化：b + c（下游）重跑，a 继承。
    new_def = _definition(
        {
            "a": _node("a"),
            "b": _node("b", after=["a"], capability="cap_b_new"),
            "c": _node("c", after=["b"]),
        },
        [WorkflowEdge(source="a", target="b"), WorkflowEdge(source="b", target="c")],
    )

    reset = compute_inherit_reset_nodes(old_def, None, new_def, None)

    assert reset == {"b", "c"}


def test_compute_inherit_reset_nodes_empty_when_nothing_changed():
    definition = _chain_definition()

    assert compute_inherit_reset_nodes(definition, None, definition, None) == set()


def test_added_node_is_reset_and_removed_node_is_not_carried_over():
    old_def = _definition({"a": _node("a")})
    new_def = _definition(
        {"a": _node("a"), "b": _node("b", after=["a"])}, [WorkflowEdge(source="a", target="b")]
    )

    reset = compute_inherit_reset_nodes(old_def, None, new_def, None)

    assert reset == {"b"}
    # 删除方向：old 有 x、new 没有 → x 不出现在结果（job_nodes 会被
    # mutation 重建为新定义的节点集）。
    new_def_without = _definition({"a": _node("a")})
    assert (
        compute_inherit_reset_nodes(
            _definition({"a": _node("a"), "x": _node("x")}), None, new_def_without, None
        )
        == set()
    )


def test_skill_latest_node_is_inherit_excluded():
    latest = _node("a", skill=WorkflowNodeSkill(key="g/n", ref="latest"))
    pinned = _node("a", skill=WorkflowNodeSkill(key="g/n", ref="v1.2.3"))
    unbound = _node("a")

    assert node_is_inherit_excluded(latest)
    assert not node_is_inherit_excluded(pinned)
    assert not node_is_inherit_excluded(unbound)


def test_shard_and_reduce_and_approval_nodes_are_inherit_excluded():
    sharded = _node("a", shard=WorkflowShardSpec(count=2))
    approval = _node("gate", node_type="approval", capability="")

    assert node_is_inherit_excluded(sharded)
    assert node_is_inherit_excluded(approval)


def test_excluded_nodes_always_reset_even_when_definitions_match():
    definition = _definition(
        {
            "a": _node("a", skill=WorkflowNodeSkill(key="g/n", ref="latest")),
            "b": _node("b", after=["a"]),
        },
        [WorkflowEdge(source="a", target="b")],
    )

    reset = compute_inherit_reset_nodes(definition, None, definition, None)

    # skill:latest 节点永远重跑；其下游因上游链哈希含该节点也重跑。
    assert "a" in reset
    assert "b" in reset


def test_upstream_rename_resets_renamed_node_but_not_content_equal_downstream():
    """上游重命名（a → a2）：a2 按新增节点重跑；b 的上游从 a 换成 a2。

    上游集哈希按 (key, hash) 列表取摘要：a 与 a2 的节点定义哈希相同
    （内容等价，key 不进哈希），但 (key, hash) 对的 key 部分不同，下游
    的上游集哈希随之变化 → b 也重跑。命名即身份：这是保守正确的取舍
    （宁可多跑，不冒上游身份漂移的险）。
    """
    old_def = _definition(
        {"a": _node("a"), "b": _node("b", after=["a"])},
        [WorkflowEdge(source="a", target="b")],
    )
    new_def = _definition(
        {"a2": _node("a2"), "b": _node("b", after=["a2"])},
        [WorkflowEdge(source="a2", target="b")],
    )

    reset = compute_inherit_reset_nodes(old_def, None, new_def, None)

    assert reset == {"a2", "b"}


def test_edge_condition_change_resets_downstream():
    old_def = _definition(
        {"a": _node("a"), "b": _node("b", after=["a"]), "c": _node("c", after=["a"])},
        [
            WorkflowEdge(source="a", target="b"),
            WorkflowEdge(
                source="a",
                target="c",
                condition=WorkflowCondition(artifact="flag.json", path="$.go", equals=True),
            ),
        ],
    )
    new_def = _definition(
        {"a": _node("a"), "b": _node("b", after=["a"]), "c": _node("c", after=["a"])},
        [WorkflowEdge(source="a", target="b"), WorkflowEdge(source="a", target="c")],
    )

    reset = compute_inherit_reset_nodes(old_def, None, new_def, None)

    assert "c" in reset


def test_branching_fanout_propagation():
    old_def = _chain_definition()
    # c 分叉出 d：只 d 是新增，其余继承。
    new_def = _definition(
        {
            "a": _node("a"),
            "b": _node("b", after=["a"]),
            "c": _node("c", after=["b"]),
            "d": _node("d", after=["c"]),
        },
        [
            WorkflowEdge(source="a", target="b"),
            WorkflowEdge(source="b", target="c"),
            WorkflowEdge(source="c", target="d"),
        ],
    )

    reset = compute_inherit_reset_nodes(old_def, None, new_def, None)

    assert reset == {"d"}


def test_workspace_config_drift_between_old_freeze_and_new_freeze_resets():
    """review P1-2：旧侧是 job 存量冻结值、新侧是 re-freeze——两份值的
    差异（workspace 配置演进）必须触发受影响节点重跑，不能因两侧同源
    re-freeze 而相等误判「未变」。"""
    definition = _chain_definition()
    old_frozen = '{"a": {"k": "v1"}}'
    new_frozen = '{"a": {"k": "v2"}}'

    reset = compute_inherit_reset_nodes(definition, old_frozen, definition, new_frozen)

    # a 的配置演进 → a 与下游闭包 b/c 全部重跑。
    assert reset == {"a", "b", "c"}


def test_matching_old_frozen_value_keeps_nodes_inheritable():
    """P1-2 配对：旧冻结值与新 re-freeze 相等 → 节点可继承（diff 空）。"""
    definition = _chain_definition()
    frozen = '{"a": {"k": "v1"}}'

    assert compute_inherit_reset_nodes(definition, frozen, definition, frozen) == set()
