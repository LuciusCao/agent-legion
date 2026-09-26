"""恢复面收窄的单元形态族：live_probe_names 的逐形态包含/排除判定。

Split from test_ready_gate_hydration_scope.py when it crossed the 800-line
test-file budget (#779 codex train review R4 follow-up); cases migrated
verbatim. The end-to-end claim-coupled tests stay in
test_ready_gate_hydration_scope.py; the shared definitions live in
tests/helpers/ready_gate_hydration.py. These cases are pure
(definition + statuses + tmp_path), hence the module-level no_db marker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.workflow_worker.input_hydration import live_probe_names
from server.app.workflows.schema import (
    WorkflowCondition,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowIntake,
    WorkflowNode,
)
from tests.helpers.ready_gate_hydration import (
    _conditional_branches_definition,
    _confluence_definition,
    _implicit_consumer_definition,
    _selected_sibling_definition,
)

pytestmark = pytest.mark.no_db


def test_condition_artifact_of_fully_decided_branch_leaves_probe_surface(tmp_path: Path) -> None:
    """#779 R4 P1：source completed 臂的过度包含——终态分支（target 已终态、
    其可达节点也全终态）的条件产物不再影响任何可运行分支，本地缓存被淘汰
    且对象丢失时不得进恢复面（否则恢复失败把整个 job 卡在 defer）。"""
    definition = _conditional_branches_definition()
    statuses = {"gate": "completed", "good": "completed", "alt": "not_applicable", "b": "pending"}

    assert "decision.json" not in live_probe_names(definition, statuses, tmp_path)


def test_condition_artifact_stays_while_verdict_still_drives_runnable_nodes(tmp_path: Path) -> None:
    """对照（当初 source-completed 臂要保的裁决稳定性）：target 已完成但
    其下游仍可运行时，条件文件在场与否仍决定 not_applicable 标记——名字
    必须留在恢复面；source completed + target pending（尚未裁决）同理。"""
    definition = _conditional_branches_definition()
    # target pending（未裁决）：条件文件必须可评估。
    pending_target = {"gate": "completed", "good": "pending", "alt": "pending", "b": "completed"}
    assert "decision.json" in live_probe_names(definition, pending_target, tmp_path)
    # target 已 completed（已选中），但同分支仍有 pending 节点时 verdict 必须
    # 稳定——给 good 接一个下游节点覆盖该形态。
    with_downstream = WorkflowDefinition(
        key="wfcond",
        label="Wf Cond",
        intake=WorkflowIntake(),
        nodes={
            **definition.nodes,
            "good_down": WorkflowNode(
                key="good_down",
                label="GoodDown",
                capability="cap_good_down",
                outputs=["gd_out.json"],
            ),
        },
        edges=[*definition.edges, WorkflowEdge(source="good", target="good_down")],
    )
    downstream_pending = {
        "gate": "completed",
        "good": "completed",
        "alt": "not_applicable",
        "good_down": "pending",
        "b": "completed",
    }
    assert "decision.json" in live_probe_names(with_downstream, downstream_pending, tmp_path)


def test_condition_artifact_excluded_when_only_implicit_consumer_runnable(tmp_path: Path) -> None:
    """#779 R4 P1 跟进：条件 verdict 的传播口径是 evaluate_branches 的显式
    边可达集（_reachable_from）。target 已终态、只有隐式消费边（node.
    inputs）可达的节点可运行时，条件 verdict 根本不影响该隐式消费者——
    合并闭包（显式 ∪ 隐式）会把它错算成「verdict 仍在驱动」，让已淘汰且
    对象丢失的条件文件每轮恢复失败、把无关 rerun 卡死在 defer。"""
    definition = _implicit_consumer_definition()
    statuses = {"gate": "completed", "good": "completed", "alt": "not_applicable", "b": "pending"}

    # b 的隐式 input 仍在恢复面（b 可运行），但已裁决分支的条件产物退出。
    names = live_probe_names(definition, statuses, tmp_path)
    assert "good_out.json" in names
    assert "decision.json" not in names


def test_confluence_via_unconditional_sibling_excludes_condition_artifact(tmp_path: Path) -> None:
    """#779 R4 P1 跟进②：条件边 s→a（a 终态）+ 无条件边 s→j + a→j 汇合，
    j 被 targeted rerun——j 经无条件边恒可达（恒在 selected 侧），条件
    verdict 的差集（unselected_reachable - selected_reachable）不覆盖它；
    条件产物已淘汰且对象丢失不得因此进恢复面阻塞 j。"""
    definition = _confluence_definition()
    statuses = {"gate": "completed", "good": "completed", "j": "pending"}

    assert "decision.json" not in live_probe_names(definition, statuses, tmp_path)
    # 对照：没有无条件兄弟边时（a→j 是唯一路径），j 的可运行性受
    # verdict 门控——decision.json 必须留在恢复面。
    edges_without_sibling = [
        edge for edge in definition.edges if not (edge.source == "gate" and edge.target == "j")
    ]
    from dataclasses import replace as _replace

    gated_only = _replace(definition, edges=edges_without_sibling)
    assert "decision.json" in live_probe_names(gated_only, statuses, tmp_path)


def test_conditional_target_with_unconditional_path_not_in_probe_surface(tmp_path: Path) -> None:
    """codex 本轮形态：s completed；条件边 s→j（decision.json）与无条件
    路径 s→u→j 汇合于 pending 的 j（targeted rerun）。j 恒经无条件路径
    可达（selected 侧），条件 verdict 不影响 j——但消费索引把 j 记作
    decision.json 的消费者，普通 input 入口（消费者可运行）会先行合入、
    差集筛选只增不减——结构性修复后 decision.json 不进恢复面。"""
    definition = WorkflowDefinition(
        key="wfc3",
        label="Wf C3",
        intake=WorkflowIntake(),
        nodes={
            "s": WorkflowNode(key="s", label="S", capability="cap_s", outputs=["decision.json"]),
            "u": WorkflowNode(key="u", label="U", capability="cap_u", outputs=["u_out.json"]),
            "j": WorkflowNode(key="j", label="J", capability="cap_j", outputs=["j_out.json"]),
        },
        edges=[
            WorkflowEdge(
                source="s",
                target="j",
                condition=WorkflowCondition("decision.json", "$.eligible", True),
            ),
            WorkflowEdge(source="s", target="u"),
            WorkflowEdge(source="u", target="j"),
        ],
    )
    statuses = {"s": "completed", "u": "completed", "j": "pending"}

    assert "decision.json" not in live_probe_names(definition, statuses, tmp_path)


def test_condition_artifact_shared_with_plain_input_follows_input_channel(tmp_path: Path) -> None:
    """对抗自查形态 (a)：条件产物名同时被普通 node.inputs 声明——input
    渠道的消费者可运行时（find_ready_nodes 真实探它），名字经 input 入口
    照常进恢复面；该消费者也终态且裁决差集为空时才退出。"""
    definition = WorkflowDefinition(
        key="wfc4",
        label="Wf C4",
        intake=WorkflowIntake(),
        nodes={
            "s": WorkflowNode(key="s", label="S", capability="cap_s", outputs=["decision.json"]),
            "a": WorkflowNode(key="a", label="A", capability="cap_a", outputs=["a_out.json"]),
            "b": WorkflowNode(
                key="b",
                label="B",
                capability="cap_b",
                inputs=["decision.json"],
                outputs=["b_out.json"],
            ),
        },
        edges=[
            WorkflowEdge(
                source="s",
                target="a",
                condition=WorkflowCondition("decision.json", "$.eligible", True),
            ),
        ],
    )
    # b 可运行：b 真实把 decision.json 当 input 探——必须进恢复面。
    runnable_consumer = {"s": "completed", "a": "completed", "b": "pending"}
    assert "decision.json" in live_probe_names(definition, runnable_consumer, tmp_path)
    # b 也终态、a 终态（差集为空）：退出。
    all_terminal = {"s": "completed", "a": "completed", "b": "completed"}
    assert "decision.json" not in live_probe_names(definition, all_terminal, tmp_path)


def test_condition_artifact_multilayer_confluence(tmp_path: Path) -> None:
    """对抗自查形态 (b)：多层汇合——条件 target a 的显式下游 x 又被无条件
    路径（s→u→x）汇合。x 恒可达（selected 侧）时条件 verdict 不门控它；
    a 已终态则 decision.json 退出恢复面。a 的下游中还有无条件路径覆盖不
    到的可运行节点 y 时，verdict 仍门控 y——必须留在恢复面。"""
    base_nodes = {
        "s": WorkflowNode(key="s", label="S", capability="cap_s", outputs=["decision.json"]),
        "a": WorkflowNode(key="a", label="A", capability="cap_a", outputs=["a_out.json"]),
        "u": WorkflowNode(key="u", label="U", capability="cap_u", outputs=["u_out.json"]),
        "x": WorkflowNode(key="x", label="X", capability="cap_x", outputs=["x_out.json"]),
    }
    base_edges = [
        WorkflowEdge(
            source="s",
            target="a",
            condition=WorkflowCondition("decision.json", "$.eligible", True),
        ),
        WorkflowEdge(source="a", target="x"),
        WorkflowEdge(source="s", target="u"),
        WorkflowEdge(source="u", target="x"),
    ]
    definition = WorkflowDefinition(
        key="wfc5", label="Wf C5", intake=WorkflowIntake(), nodes=base_nodes, edges=base_edges
    )
    # a 终态、x 可运行但恒经无条件路径可达 → 退出。
    statuses = {"s": "completed", "a": "completed", "u": "completed", "x": "pending"}
    assert "decision.json" not in live_probe_names(definition, statuses, tmp_path)

    # a 的下游 y 不被无条件路径覆盖且可运行 → verdict 仍门控 y → 留在恢复面。
    nodes_with_y = {
        **base_nodes,
        "y": WorkflowNode(key="y", label="Y", capability="cap_y", outputs=["y_out.json"]),
    }
    with_y = WorkflowDefinition(
        key="wfc5",
        label="Wf C5",
        intake=WorkflowIntake(),
        nodes=nodes_with_y,
        edges=[*base_edges, WorkflowEdge(source="a", target="y")],
    )
    statuses_y = {**statuses, "y": "pending"}
    assert "decision.json" in live_probe_names(with_y, statuses_y, tmp_path)


def test_selected_conditional_sibling_covers_verdict_difference(tmp_path: Path) -> None:
    """codex 本轮形态：A（s→a，a.json 本地缺失）的显式可达集被当前选中的
    条件兄弟边 B（s→b，b.json 在场且选中，b→a→j）覆盖——j pending
    （targeted rerun）。evaluate_branches 把 B 的选中可达集纳入
    selected_reachable，A 缺失不影响 j——a.json 不进恢复面。只扣无条件
    兄弟的修复前形态会把 a.json 留在恢复面（对象丢失则每轮 defer）。"""
    definition = _selected_sibling_definition()
    statuses = {
        "s": "completed",
        "p": "completed",
        "b": "completed",
        "a": "completed",
        "j": "pending",
    }
    (tmp_path / "b.json").write_text('{"ok": true}', encoding="utf-8")

    assert "a.json" not in live_probe_names(definition, statuses, tmp_path)


def test_unselected_or_undecidable_sibling_puts_artifact_back(tmp_path: Path) -> None:
    """对抗自查（翻转形态）：选中兄弟边 B 后来未选中/不可判定时 A 回到
    恢复面——b.json 缺失（按文件语义判未选中），或 B 条件文件的生产者
    在途（p pending → B 推迟、不进 selected 侧）。"""
    definition = _selected_sibling_definition()
    statuses = {
        "s": "completed",
        "p": "completed",
        "b": "completed",
        "a": "completed",
        "j": "pending",
    }
    # b.json 缺失：B 未选中 → A 的可达集无覆盖 → 回到恢复面。
    assert "a.json" in live_probe_names(definition, statuses, tmp_path)

    # B 的条件文件生产者在途（p pending）→ B 推迟（不可判定、不选中）→
    # 同样不覆盖（b.json 在场也没用）。
    (tmp_path / "b.json").write_text('{"ok": true}', encoding="utf-8")
    producer_in_flight = {**statuses, "p": "pending"}
    assert "a.json" in live_probe_names(definition, producer_in_flight, tmp_path)
