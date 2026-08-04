from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from server.app.workflows.conditions import selected_edges
from server.app.workflows.definition import WorkflowDefinition, WorkflowEdge

RUNNABLE_STATUSES = {"pending", "ready", "stale"}


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
    outgoing: dict[str, list[WorkflowEdge]] = {key: [] for key in definition.nodes}
    for edge in definition.edges:
        outgoing[edge.source].append(edge)
    for node_key, edges in outgoing.items():
        if node_statuses.get(node_key) != "completed":
            continue
        if not any(edge.condition is not None for edge in edges):
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
