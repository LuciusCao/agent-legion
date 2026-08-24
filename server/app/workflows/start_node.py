"""Start node: the workflow entry contract carrier (EXEC-WORKFLOW-START-001).

Every definition has exactly one ``type: start`` node. It declares
``accepted_item_types`` (the intake contract enforced by RunService), never
executes — the scheduler treats it as completed and it never enters
job_nodes — and cannot be removed: definitions without one get a synthetic
start injected at parse time (D3), so pre-start revisions and job snapshots
keep their exact readiness behavior with zero migration.
"""

from __future__ import annotations

from typing import Any

from server.app.workflows.schema import (
    DEFAULT_ACCEPTED_ITEM_TYPES,
    WorkflowDefinitionError,
    WorkflowEdge,
    WorkflowNode,
)

START_NODE_TYPE = "start"
_FORBIDDEN_START_FIELDS = ("capability", "execution", "shard", "reduce", "terminal")


def load_start_fields(raw_node: dict[str, Any], node_key: str) -> tuple[str, tuple[str, ...]]:
    """Parse ``type``/``accepted_item_types`` and enforce the start-only field rules."""
    node_type = raw_node.get("type", "node")
    if node_type not in ("node", START_NODE_TYPE):
        raise WorkflowDefinitionError(f"Node {node_key} type must be 'node' or {START_NODE_TYPE!r}")
    raw_types = raw_node.get("accepted_item_types")
    if node_type != START_NODE_TYPE:
        if raw_types is not None:
            raise WorkflowDefinitionError(
                f"Node {node_key}.accepted_item_types is only valid on a start node"
            )
        return node_type, DEFAULT_ACCEPTED_ITEM_TYPES
    for forbidden in _FORBIDDEN_START_FIELDS:
        if forbidden in raw_node:
            raise WorkflowDefinitionError(f"Start node {node_key} must not declare {forbidden}")
    if raw_types is None:
        return node_type, DEFAULT_ACCEPTED_ITEM_TYPES
    if (
        not isinstance(raw_types, list)
        or not raw_types
        or any(item not in DEFAULT_ACCEPTED_ITEM_TYPES for item in raw_types)
    ):
        raise WorkflowDefinitionError(
            f"Start node {node_key}.accepted_item_types must be a non-empty subset"
            f" of {list(DEFAULT_ACCEPTED_ITEM_TYPES)}"
        )
    return node_type, tuple(dict.fromkeys(raw_types))


def ensure_start_node(
    nodes: dict[str, WorkflowNode], edges: list[WorkflowEdge]
) -> tuple[dict[str, WorkflowNode], list[WorkflowEdge]]:
    """Validate the explicit start node, or inject a synthetic one when absent."""
    start_keys = [key for key, node in nodes.items() if node.node_type == START_NODE_TYPE]
    if len(start_keys) > 1:
        raise WorkflowDefinitionError(
            f"Workflow must declare exactly one start node; found {len(start_keys)}"
        )
    if not start_keys:
        return _inject_start_node(nodes, edges)
    start_key = start_keys[0]
    if any(edge.target == start_key for edge in edges):
        raise WorkflowDefinitionError(f"Start node {start_key} must not have incoming edges")
    if not any(edge.source == start_key for edge in edges):
        raise WorkflowDefinitionError(
            f"Start node {start_key} must have at least one outgoing edge"
        )
    # A start node never executes, so a ``when`` artifact referenced by a
    # conditional outgoing edge can never exist: the edge would never be
    # selected and the whole branch would be marked not_applicable. Reject
    # the condition at load time instead of silently running nothing.
    if any(edge.source == start_key and edge.condition is not None for edge in edges):
        raise WorkflowDefinitionError(
            f"Start node {start_key} must not have conditional outgoing edges"
        )
    return nodes, edges


def _inject_start_node(
    nodes: dict[str, WorkflowNode], edges: list[WorkflowEdge]
) -> tuple[dict[str, WorkflowNode], list[WorkflowEdge]]:
    """Synthetic start: accepts every item type and points at all implicit roots."""
    key = "_start"
    suffix = 0
    while key in nodes:
        suffix += 1
        key = f"_start_{suffix}"
    start = WorkflowNode(key=key, label="Start", capability="", node_type=START_NODE_TYPE)
    injected_edges = [
        WorkflowEdge(source=key, target=node_key)
        for node_key in nodes
        if not any(edge.target == node_key for edge in edges)
    ]
    # Appended (not prepended) so snapshot round-trips of v1 definitions —
    # whose materialized edges reload after the ``after``-derived ones —
    # reproduce the exact same edge order (D3 hash symmetry).
    return {key: start, **nodes}, [*edges, *injected_edges]
