"""Edge-level diffing for workflow draft compare (#431 split).

The identity diff (set semantics) lives with the node diff in
``workflow_draft_compare``; this module owns the order-sensitive pieces the
identity set cannot see: duplicate source/target detection and the edges
reorder — a same-set-different-order draft that ``_structural_payload``
publishes as a new revision.
"""

from __future__ import annotations

from typing import Any

from server.app.services.workflow_draft_compare_conditions import (
    condition_identity as _condition_identity,
)
from server.app.services.workflow_draft_compare_conditions import (
    format_condition as _format_condition,
)
from server.app.workflows.schema import WorkflowDefinition, WorkflowEdge


def _edge_identity(edge: WorkflowEdge, use_full: bool) -> str:
    if use_full:
        return f"{edge.source}|{edge.target}|{_condition_identity(edge.condition)}"
    return f"{edge.source}|{edge.target}"


def _detect_duplicate_source_target(edges: list[WorkflowEdge]) -> bool:
    seen: set[str] = set()
    for edge in edges:
        key = f"{edge.source}|{edge.target}"
        if key in seen:
            return True
        seen.add(key)
    return False


def _should_use_full_edge_identity(base: WorkflowDefinition, draft: WorkflowDefinition) -> bool:
    return _detect_duplicate_source_target(base.edges) or _detect_duplicate_source_target(
        draft.edges
    )


def _diff_edge_order(
    base: WorkflowDefinition,
    draft: WorkflowDefinition,
    use_full: bool,
    edge_changes: list[dict[str, Any]],
    risk_flags: list[dict[str, Any]],
) -> None:
    """Issue #431: report an edges reorder (same set, different order).

    It is invisible to the identity diff below but structural to the publish
    path (``_structural_payload`` compares the edges list with ordered
    ``==``), so it would publish a new revision while compare reports zero
    changes. Reported once at the definition level through the edges
    dimension: node-level ``after`` does not mirror it — the two orders are
    independent (a schema_version 2 definition derives edges from the
    ``edges:`` block, not from ``after``), and a moved edge has no home node
    (its source and target both keep their neighbors).
    """
    base_order = [_edge_identity(edge, use_full) for edge in base.edges]
    draft_order = [_edge_identity(edge, use_full) for edge in draft.edges]
    if sorted(base_order) != sorted(draft_order) or base_order == draft_order:
        return
    first = base.edges[0]
    edge_changes.append(
        {
            "type": "reordered",
            "source": first.source,
            "target": first.target,
            "before_condition": None,
            "after_condition": None,
            "risk": "info",
        }
    )
    risk_flags.append(
        {
            "code": "edges_reordered",
            "severity": "info",
            "message": "边的顺序发生变化（边集合不变）：结构快照随顺序更新，不影响运行路径。",
        }
    )


def _diff_edge_identities(
    base: WorkflowDefinition,
    draft: WorkflowDefinition,
    use_full: bool,
    edge_changes: list[dict[str, Any]],
    risk_flags: list[dict[str, Any]],
) -> None:
    base_edges = {_edge_identity(edge, use_full): edge for edge in base.edges}
    draft_edges = {_edge_identity(edge, use_full): edge for edge in draft.edges}

    for key in sorted(set(base_edges) | set(draft_edges)):
        base_edge = base_edges.get(key)
        draft_edge = draft_edges.get(key)

        if base_edge is None and draft_edge is not None:
            edge_changes.append(
                {
                    "type": "added",
                    "source": draft_edge.source,
                    "target": draft_edge.target,
                    "before_condition": None,
                    "after_condition": _format_condition(draft_edge.condition),
                    "risk": "warning",
                }
            )
            risk_flags.append(
                {
                    "code": "edge_added",
                    "severity": "warning",
                    "message": f"新增边 {draft_edge.source} -> {draft_edge.target}。",
                }
            )
            continue

        if base_edge is not None and draft_edge is None:
            edge_changes.append(
                {
                    "type": "removed",
                    "source": base_edge.source,
                    "target": base_edge.target,
                    "before_condition": _format_condition(base_edge.condition),
                    "after_condition": None,
                    "risk": "breaking",
                }
            )
            risk_flags.append(
                {
                    "code": "edge_removed",
                    "severity": "breaking",
                    "message": f"边 {base_edge.source} -> {base_edge.target} 被删除。",
                }
            )
            continue

        if base_edge is not None and draft_edge is not None and not use_full:
            base_condition = _format_condition(base_edge.condition)
            draft_condition = _format_condition(draft_edge.condition)
            if base_condition != draft_condition:
                edge_changes.append(
                    {
                        "type": "condition_changed",
                        "source": base_edge.source,
                        "target": base_edge.target,
                        "before_condition": base_condition,
                        "after_condition": draft_condition,
                        "risk": "breaking",
                    }
                )
                risk_flags.append(
                    {
                        "code": "edge_condition_changed",
                        "severity": "breaking",
                        "message": "分支条件变化会改变运行路径。",
                    }
                )


def diff_edges(
    base: WorkflowDefinition,
    draft: WorkflowDefinition,
    edge_changes: list[dict[str, Any]],
    risk_flags: list[dict[str, Any]],
) -> None:
    use_full = _should_use_full_edge_identity(base, draft)
    _diff_edge_order(base, draft, use_full, edge_changes, risk_flags)
    _diff_edge_identities(base, draft, use_full, edge_changes, risk_flags)
