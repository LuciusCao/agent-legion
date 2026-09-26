from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from server.app.workflows.condition_barrier import branch_gated_keys, condition_producer_in_flight
from server.app.workflows.conditions import selected_edges
from server.app.workflows.definition import WorkflowDefinition, WorkflowEdge
from server.app.workflows.workflow_consumption import artifact_producers, dependency_downstream

RUNNABLE_STATUSES = {"pending", "ready", "stale"}


def effective_node_statuses(
    definition: WorkflowDefinition, node_statuses: dict[str, str]
) -> dict[str, str]:
    """Node statuses with start nodes overlaid as completed (EXEC-WORKFLOW-START-001).

    Start nodes never enter job_nodes, so their row is always absent; the
    overlay makes their outgoing edges satisfiable without a dispatch path.
    """
    statuses = dict(node_statuses)
    for node in definition.nodes.values():
        if node.node_type == "start":
            statuses[node.key] = "completed"
    return statuses


@dataclass(frozen=True)
class BranchEvaluation:
    not_applicable: set[str]


class EdgeVerdict(NamedTuple):
    """一组出边（同一 source）的裁决结果。"""

    selected: frozenset[str]
    unselected: frozenset[str]  # 可判定但未选中
    deferred: frozenset[str]  # 条件文件生产者在途（本轮不可判）


def evaluate_edge_verdict(
    definition: WorkflowDefinition,
    edges: list[WorkflowEdge],
    node_statuses: dict[str, str],
    artifact_dir: Path,
) -> EdgeVerdict:
    """逐边裁决代数（可判定/选中/推迟）的唯一实现（#779 列车 R4 复审 P1
    跟进④）：生产者在途的条件边进 deferred（本轮不可判）；可判定边经
    ``selected_edges`` 按本地字节裁决；无条件边恒 selected。

    共享纪律：``evaluate_branches``（本文件下方）的 not_applicable 差集
    与 ``live_probe_names``（workflow_worker/input_hydration）的恢复面
    筛选都从本函数取判定结果——两侧不得各自重写这套代数；本函数的语义
    变动必须同步检查另一侧。
    """
    producers = artifact_producers(definition)
    decidable: list[WorkflowEdge] = []
    deferred: set[str] = set()
    for edge in edges:
        if condition_producer_in_flight(
            edge, producers, node_statuses, excluded=branch_gated_keys(definition, edge.target)
        ):
            deferred.add(edge.target)
        else:
            decidable.append(edge)
    selected = {edge.target for edge in selected_edges(decidable, artifact_dir)}
    unselected = {edge.target for edge in decidable} - selected
    return EdgeVerdict(frozenset(selected), frozenset(unselected), frozenset(deferred))


def _reachable_from(definition: WorkflowDefinition, start_keys: set[str]) -> set[str]:
    """显式边传递可达集（含起点本身；``downstream_nodes`` 的多起点形态）。"""
    seen = set(start_keys)
    for key in start_keys:
        seen.update(downstream_nodes(definition, key))
    return seen


def evaluate_branches(
    definition: WorkflowDefinition,
    node_statuses: dict[str, str],
    artifact_dir: Path,
) -> BranchEvaluation:
    not_applicable: set[str] = set()
    deferred: set[str] = set()  # 在途条件边的 target 可达集（本轮不可标）
    node_statuses = effective_node_statuses(definition, node_statuses)
    outgoing: dict[str, list[WorkflowEdge]] = {key: [] for key in definition.nodes}
    for edge in definition.edges:
        outgoing[edge.source].append(edge)
    for node_key, edges in outgoing.items():
        if node_statuses.get(node_key) != "completed":
            continue
        if not any(edge.condition is not None for edge in edges):
            continue
        # 逐边裁决代数（可判定/选中/推迟）走共享实现 evaluate_edge_verdict
        # （本文件上方，#779 列车 R4 复审 P1 跟进④）——hydration 的
        # 恢复面筛选用同一函数，两侧不各自维护集合代数。逐边推迟语义
        # （#759 ③ 终审 P1）不变：生产者在途的边进 deferred（其 target
        # 可达集本轮不可标），可判定的边照常裁决。
        verdict = evaluate_edge_verdict(definition, edges, node_statuses, artifact_dir)
        selected_reachable = _reachable_from(definition, set(verdict.selected))
        unselected_reachable = _reachable_from(definition, set(verdict.unselected))
        not_applicable.update(unselected_reachable - selected_reachable)
        for target in verdict.deferred:
            deferred |= {target} | set(dependency_downstream(definition, target))
    return BranchEvaluation(not_applicable=not_applicable - deferred)


def _incoming_edges(definition: WorkflowDefinition) -> dict[str, list[WorkflowEdge]]:
    incoming: dict[str, list[WorkflowEdge]] = {key: [] for key in definition.nodes}
    for edge in definition.edges:
        incoming[edge.target].append(edge)
    return incoming


def upstream_nodes(definition: WorkflowDefinition, node_key: str) -> list[str]:
    """Direct upstream (parent) nodes of node_key, in edge declaration order."""
    return list(dict.fromkeys(edge.source for edge in definition.edges if edge.target == node_key))


def downstream_nodes(definition: WorkflowDefinition, node_key: str) -> list[str]:
    children: dict[str, list[str]] = {key: [] for key in definition.nodes}
    for edge in definition.edges:
        children[edge.source].append(edge.target)
    seen: set[str] = set()
    ordered: list[str] = []
    stack = list(children.get(node_key, []))
    while stack:
        child = stack.pop(0)
        if child in seen:
            continue
        seen.add(child)
        ordered.append(child)
        stack.extend(children.get(child, []))
    return ordered
