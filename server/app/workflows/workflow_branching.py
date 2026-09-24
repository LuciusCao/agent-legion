from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from server.app.workflows.condition_barrier import any_condition_producer_in_flight
from server.app.workflows.conditions import selected_edges
from server.app.workflows.definition import WorkflowDefinition, WorkflowEdge
from server.app.workflows.workflow_consumption import artifact_producers

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
    deferred: set[str] = set()  # 推迟 source 的条件 target 可达集（本轮不可标）
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
        if any_condition_producer_in_flight(edges, producers, node_statuses, definition):
            # 条件产物的生产者在途（重跑/尚未产出）：缺失或旧字节都不可信，
            # 推迟整个 source 的分支裁决——不选边、不把任何 target 标成
            # not_applicable（终态无复活路径），等生产者完成后用新字节评估。
            # 其条件 target 的可达集进 deferred：多源汇合拓扑下，其他 source
            # 本轮的合法评估同样不能把该 target 钉死（本 source 之后仍可能
            # 选中它，#759 ③ 二轮对抗复审 P2）。
            deferred |= _reachable_from(
                definition, {edge.target for edge in edges if edge.condition is not None}
            )
            continue
        selected = selected_edges(edges, artifact_dir)
        selected_targets = {edge.target for edge in selected}
        unselected_targets = {edge.target for edge in edges} - selected_targets
        selected_reachable = _reachable_from(definition, selected_targets)
        unselected_reachable = _reachable_from(definition, unselected_targets)
        not_applicable.update(unselected_reachable - selected_reachable)
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
