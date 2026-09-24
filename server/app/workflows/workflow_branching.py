from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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
    deferred: set[str] = set()  # 在途条件边的 target 可达集（本轮不可标）
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
        # 逐边推迟（#759 ③ 终审 P1）：生产者在途的边进 deferred（其 target
        # 可达集本轮不可标），可判定的边照常裁决——整源推迟会把可判定的
        # 兄弟边挟持住：兄弟 target 不钉死 → 其分支内的生产者永远跑不到
        # → 在途永不解除（永久静默挂起，基线行为是可终止）。
        decidable: list[WorkflowEdge] = []
        for edge in edges:
            if condition_producer_in_flight(
                edge,
                producers,
                node_statuses,
                excluded=branch_gated_keys(definition, edge.target),
            ):
                deferred |= {edge.target} | set(dependency_downstream(definition, edge.target))
            else:
                decidable.append(edge)
        if not decidable:
            continue
        selected = selected_edges(decidable, artifact_dir)
        selected_targets = {edge.target for edge in selected}
        unselected_targets = {edge.target for edge in decidable} - selected_targets
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
