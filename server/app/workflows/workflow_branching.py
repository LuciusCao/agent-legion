from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from server.app.workflows.conditions import selected_edges
from server.app.workflows.definition import WorkflowDefinition, WorkflowEdge
from server.app.workflows.workflow_consumption import artifact_producers

RUNNABLE_STATUSES = {"pending", "ready", "stale"}
TERMINAL_SUCCESS_STATUSES = {"completed", "not_applicable"}


def condition_producer_in_flight(
    edge: WorkflowEdge,
    producers: dict[str, set[str]],
    node_statuses: dict[str, str],
) -> bool:
    """分支条件产物的生产者屏障（#759 ③ 对抗复审 P1）：条件 artifact 有非
    终态生产者时，该边的条件判定不可信——缺失（暂存删除/尚未产出）会被
    ``condition_matches`` 当 false（gated 分支被永久标 not_applicable），
    保留的旧字节（RMW）会被当真值走错分支。判定推迟到生产者终态之后：
    不选边、不标 not_applicable。

    与调度就绪的隐式生产者屏障（scheduler.find_ready_nodes 的
    ``_has_unfinished_implicit_producer``）共用同一张生产者索引
    （``artifact_producers``）与同一组终态集合：not_applicable 生产者不设
    障（其产物本轮不刷新，读既有文件与文件存在语义一致）；failed 生产者
    设障无害（failed-upstream 防线会先拦住整条支路，job 已失败）。
    """
    if edge.condition is None:
        return False
    for producer in producers.get(edge.condition.artifact, ()):
        if node_statuses.get(producer, "pending") not in TERMINAL_SUCCESS_STATUSES:
            return True
    return False


def any_condition_producer_in_flight(
    edges: Iterable[WorkflowEdge],
    producers: dict[str, set[str]],
    node_statuses: dict[str, str],
) -> bool:
    """``condition_producer_in_flight`` 的集合版（调度就绪闸逐入边判定）。"""
    return any(condition_producer_in_flight(edge, producers, node_statuses) for edge in edges)


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


def _reachable_from(definition: WorkflowDefinition, start_keys: set[str]) -> set[str]:
    children: dict[str, list[str]] = {key: [] for key in definition.nodes}
    for edge in definition.edges:
        children[edge.source].append(edge.target)
    seen: set[str] = set()
    stack = list(start_keys)
    while stack:
        key = stack.pop()
        if key in seen:
            continue
        seen.add(key)
        stack.extend(children.get(key, []))
    return seen


def evaluate_branches(
    definition: WorkflowDefinition,
    node_statuses: dict[str, str],
    artifact_dir: Path,
) -> BranchEvaluation:
    not_applicable: set[str] = set()
    node_statuses = effective_node_statuses(definition, node_statuses)
    producers = artifact_producers(definition)
    outgoing: dict[str, list[WorkflowEdge]] = {key: [] for key in definition.nodes}
    for edge in definition.edges:
        outgoing[edge.source].append(edge)
    for node_key, edges in outgoing.items():
        if node_statuses.get(node_key) != "completed":
            continue
        if not any(edge.condition is not None for edge in edges):
            continue
        if any(condition_producer_in_flight(edge, producers, node_statuses) for edge in edges):
            # 条件产物的生产者在途（重跑/尚未产出）：缺失或旧字节都不可信，
            # 推迟整个 source 的分支裁决——不选边、不把任何 target 标成
            # not_applicable（终态无复活路径），等生产者完成后用新字节评估。
            continue
        selected = selected_edges(edges, artifact_dir)
        selected_targets = {edge.target for edge in selected}
        unselected_targets = {edge.target for edge in edges} - selected_targets
        selected_reachable = _reachable_from(definition, selected_targets)
        unselected_reachable = _reachable_from(definition, unselected_targets)
        not_applicable.update(unselected_reachable - selected_reachable)
    return BranchEvaluation(not_applicable=not_applicable)


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
