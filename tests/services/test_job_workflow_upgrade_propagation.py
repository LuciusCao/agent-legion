"""传播闭包与种子收集的纯函数测试（issue #645，702 传播闭包重构）。

全部不触库：``collect_change_seeds``（S1–S5 纯局部种子）与
``rerun_closure``（通道 A 边传播 + 通道 B 同名 fixpoint）的判定面。
等价性声明由既有 74 用例（diff/inherit/codex3/codex4/mutation/routes，
断言零改动）承重；本文件钉住新模块的机制面——种子判定的维度切换、
双通道级联、以及判别力反转（去传播必红，防未来种子漏接）。
"""

from __future__ import annotations

import pytest

from server.app.services.job_artifact_staging_scope import staging_output_names
from server.app.services.job_workflow_upgrade_propagation import (
    collect_change_seeds,
    rerun_closure,
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


def _chain() -> WorkflowDefinition:
    """a → b → c 三级链。"""
    return _definition(
        {
            "a": _node("a"),
            "b": _node("b", after=["a"]),
            "c": _node("c", after=["b"]),
        },
        [WorkflowEdge(source="a", target="b"), WorkflowEdge(source="b", target="c")],
    )


# ---------------------------------------------------------------------------
# S1–S5 种子判定（各一）+ 零种子
# ---------------------------------------------------------------------------


def test_seed_s1_definition_change() -> None:
    """S1：节点定义哈希漂移 → 种子；新增节点（旧快照缺失）→ 种子。"""
    old_def = _chain()
    new_def = _definition(
        {
            "a": _node("a"),
            "b": _node("b", after=["a"], capability="cap_b_new"),
            "c": _node("c", after=["b"]),
        },
        [WorkflowEdge(source="a", target="b"), WorkflowEdge(source="b", target="c")],
    )

    seeds = collect_change_seeds(old_def, None, new_def, None)

    assert "b" in seeds
    assert "a" not in seeds

    # 新增节点形态：d 不在旧快照。
    new_with_added = _definition(
        {**new_def.nodes, "d": _node("d", after=["c"])},
        [
            WorkflowEdge(source="a", target="b"),
            WorkflowEdge(source="b", target="c"),
            WorkflowEdge(source="c", target="d"),
        ],
    )
    assert "d" in collect_change_seeds(new_def, None, new_with_added, None)


def test_seed_s2_frozen_config_drift() -> None:
    """S2：frozen config 段新旧不等 → 种子（只命中该节点）。"""
    definition = _chain()

    seeds = collect_change_seeds(definition, '{"a": {"k": "v1"}}', definition, '{"a": {"k": "v2"}}')

    assert seeds == {"a"}


def test_seed_s3_incoming_edge_change() -> None:
    """S3：入边声明（when 条件）变化 → 种子（调度语义变化）。"""
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

    seeds = collect_change_seeds(old_def, None, new_def, None)

    assert "c" in seeds
    assert "a" not in seeds and "b" not in seeds


def test_seed_s4_implementation_excluded() -> None:
    """S4：实现身份排除集原样并入种子（plan 层解析好传入）。"""
    definition = _chain()

    seeds = collect_change_seeds(definition, None, definition, None, frozenset({"b"}))

    assert "b" in seeds


def test_seed_s5_inherit_exclusion_rules() -> None:
    """S5：排除规则节点（skill:latest / 分片）进种子。"""
    definition = _definition(
        {
            "a": _node("a", skill=WorkflowNodeSkill(key="g/n", ref="latest")),
            "b": _node("b", shard=WorkflowShardSpec(count=2)),
            "c": _node("c", after=["a"]),
        },
        [WorkflowEdge(source="a", target="c")],
    )

    seeds = collect_change_seeds(definition, None, definition, None)

    assert "a" in seeds and "b" in seeds
    assert "c" not in seeds


def test_no_seeds_when_nothing_changed() -> None:
    """零种子：定义、config、入边、排除全部一致 → 空种子集（全继承）。"""
    definition = _chain()

    assert collect_change_seeds(definition, None, definition, None) == set()
    assert (
        collect_change_seeds(definition, '{"a": {"k": "v"}}', definition, '{"a": {"k": "v"}}')
        == set()
    )


# ---------------------------------------------------------------------------
# rerun_closure：通道 A 传播面
# ---------------------------------------------------------------------------


def test_closure_fanout_propagation() -> None:
    """通道 A fanout：种子的全下游（多分支）一起进闭包。"""
    definition = _definition(
        {
            "a": _node("a"),
            "b": _node("b", after=["a"]),
            "c": _node("c", after=["a"]),
            "d": _node("d", after=["b"]),
            "e": _node("e", after=["c"]),
        },
        [
            WorkflowEdge(source="a", target="b"),
            WorkflowEdge(source="a", target="c"),
            WorkflowEdge(source="b", target="d"),
            WorkflowEdge(source="c", target="e"),
        ],
    )

    closure = rerun_closure(definition, {"a"})

    assert closure == {"a", "b", "c", "d", "e"}


def test_closure_conditional_edge_downstream_included() -> None:
    """条件边下游也在 children map 里：种子经条件边传播（分支语义不裁剪闭包）。"""
    definition = _definition(
        {"a": _node("a"), "b": _node("b"), "c": _node("c", after=["a"])},
        [
            WorkflowEdge(
                source="a",
                target="c",
                condition=WorkflowCondition(artifact="flag.json", path="$.go", equals=True),
            ),
            WorkflowEdge(source="b", target="c"),
        ],
    )

    closure = rerun_closure(definition, {"a"})

    assert "c" in closure
    assert "b" not in closure


def test_closure_clamps_to_executable_nodes() -> None:
    """闭包限新图可执行节点：start 节点（不执行）不进闭包；删除节点不保留。"""
    definition = _definition(
        {
            "start": _node("start", node_type="start", capability=""),
            "a": _node("a", after=["start"]),
        },
        [WorkflowEdge(source="start", target="a")],
    )

    closure = rerun_closure(definition, {"a"})

    assert closure == {"a"}


# ---------------------------------------------------------------------------
# rerun_closure：通道 B（名字共享）与双通道级联
# ---------------------------------------------------------------------------


def _diamond_with_shared_name() -> WorkflowDefinition:
    """a → b、a → c，b 与 c 共享纯输出名 out.json（对象键冲突面）。"""
    return _definition(
        {
            "a": _node("a"),
            "b": _node("b", after=["a"], outputs=["out.json"]),
            "c": _node("c", after=["a"], outputs=["out.json"]),
            "d": _node("d", after=["c"], outputs=["d_out.json"]),
        },
        [
            WorkflowEdge(source="a", target="b"),
            WorkflowEdge(source="a", target="c"),
            WorkflowEdge(source="c", target="d"),
        ],
    )


def test_closure_shared_name_producer_reruns_together() -> None:
    """通道 B：候选与重置面共享纯输出名 → 候选一起重跑（对象键无 node 身份）。"""
    definition = _diamond_with_shared_name()

    closure = rerun_closure(definition, {"b"})

    # b 是种子；c 与 b 共享 out.json → c 进闭包；d 是 c 的下游 → 边通道级联。
    assert closure == {"b", "c", "d"}


def test_closure_shared_name_includes_rmw_producer() -> None:
    """RMW outputs still own the shared object key and cannot remain inherited."""
    definition = _definition(
        {
            "pure": _node("pure", outputs=["shared.json"]),
            "rmw": _node(
                "rmw",
                inputs=["shared.json"],
                outputs=["shared.json"],
            ),
            "child": _node("child", after=["rmw"]),
        },
        [WorkflowEdge(source="rmw", target="child")],
    )

    assert rerun_closure(definition, {"pure"}) == {"pure", "rmw", "child"}


def test_staging_shared_pure_output_preserves_affected_rmw_input() -> None:
    """A shared pure producer must not move a reset RMW node's startup input."""
    definition = _definition(
        {
            "pure": _node("pure", outputs=["shared.json"]),
            "rmw": _node(
                "rmw",
                inputs=["shared.json"],
                outputs=["shared.json"],
            ),
        }
    )

    assert staging_output_names(definition, {"pure", "rmw"}) == set()


def test_staging_does_not_strand_outside_rmw_producer() -> None:
    """An inherited RMW producer protects its shared path from staging."""
    definition = _definition(
        {
            "pure": _node("pure", outputs=["shared.json"]),
            "rmw": _node(
                "rmw",
                inputs=["shared.json"],
                outputs=["shared.json"],
            ),
        }
    )

    assert staging_output_names(definition, {"pure"}) == set()


def test_closure_name_and_edge_dual_channel_cascade() -> None:
    """双通道级联（对照 codex3 test_shared_name_rerun_closure_cascades_to_downstream）：

    名字排除扩大重置面 → 新排除节点的下游再进边通道——fixpoint 收敛。
    形态：种子 x 与候选 a 共享 out.json → a 经通道 B 排除；a 的下游 b
    经通道 A 级联；独立节点 y 不受波及。
    """
    definition = _definition(
        {
            "a": _node("a", outputs=["out.json"]),
            "b": _node("b", after=["a"]),
            "c": _node("c", after=["b"]),
            "x": _node("x", outputs=["out.json"]),
            "y": _node("y", outputs=["y.json"]),
        },
        [
            WorkflowEdge(source="a", target="b"),
            WorkflowEdge(source="b", target="c"),
        ],
    )

    closure = rerun_closure(definition, {"x"})

    assert closure == {"x", "a", "b", "c"}
    # 独立节点 y（无共享名、无边关系）保持继承。
    assert "y" not in closure


def test_closure_s6_seed_merge_recomputes() -> None:
    """S6 种子并入后再闭包（plan 层接线形态）：unreachable 候选作为种子
    重算闭包，其下游与同名候选一并移出继承集。"""
    definition = _diamond_with_shared_name()

    first = rerun_closure(definition, {"a"})
    candidates = frozenset(definition.executable_nodes) - first
    # 假设 b 的产物不可达（S6 探测）：并入种子再闭包。
    merged = rerun_closure(definition, {"a"} | ({"b"} & candidates))

    assert merged == {"a", "b", "c", "d"}


# ---------------------------------------------------------------------------
# 判别力反转：去传播必红（钉死「种子必传播」的机制）
# ---------------------------------------------------------------------------


def test_closure_without_propagation_would_lose_downstream() -> None:
    """判别力反转：``rerun_closure`` 退化为 ``set(seeds)``（不传播）时，
    下游节点会被错误保留在继承集——本用例经等价改写钉死该退化。

    直接断言：闭包严格大于种子集（含下游），且去掉任一下游传播都会
    破坏「种子 → 全下游」的不变量。防未来种子漏接的结构性防线。
    """
    definition = _chain()
    seeds = {"a"}

    closure = rerun_closure(definition, seeds)

    assert closure == {"a", "b", "c"}
    # 反转验证：退化闭包（不传播）丢失 b/c——正是用户反例的形态。
    degraded = {key for key in seeds if key in definition.executable_nodes}
    assert degraded == {"a"}
    assert degraded < closure


def test_seeds_missing_downstream_is_the_counterexample_shape() -> None:
    """用户反例的结构性防复发：实现身份种子（S4）必须传播到全下游。

    A(code)→B(agent)→C 形态：A 的实现漂移只有 A 是种子，但闭包必须把
    B/C 带进重置面——「下游的 upstream 一致性」由闭包直接定义，不再
    依赖定义侧哈希链的隐式吸收。
    """
    definition = _chain()
    seeds = collect_change_seeds(definition, None, definition, None, frozenset({"a"}))

    closure = rerun_closure(definition, seeds)

    assert closure == {"a", "b", "c"}


@pytest.mark.no_db
def test_propagation_is_pure() -> None:
    """纯函数性：同输入两次调用产出相同闭包（无隐藏状态）。"""
    definition = _diamond_with_shared_name()

    one = rerun_closure(definition, {"b"})
    two = rerun_closure(definition, {"b"})

    assert one == two == {"b", "c", "d"}
