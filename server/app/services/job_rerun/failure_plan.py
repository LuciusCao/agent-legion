"""Shared read-only target planning for failure-category reruns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from server.app.services.job_operation_error import JobOperationError, JobOperationResult
from server.app.services.job_rerun.preview_checks import rerun_ineligible_from_nodes
from server.app.workflows.start_node import START_NODE_TYPE
from server.app.workflows.workflow_consumption import dependency_downstream, dependency_parents
from server.app.workflows.workflow_failure_policy import (
    DEFAULT_ON_FAILURE,
    resolve_node_rerun_strategy,
)


@dataclass(frozen=True)
class FailureRerunPlan:
    targets: tuple[str, ...] = ()
    entries: tuple[str | JobOperationResult, ...] = ()
    error: JobOperationError | None = None

    @property
    def planned_targets(self) -> list[str]:
        return [entry for entry in self.entries if isinstance(entry, str)]


def resolve_failure_rerun_targets(
    definition: Any,
    failed_nodes: list[str],
    category: str,
    strategy: str,
    from_node_key: str | None,
    job_id: str,
) -> tuple[tuple[str, ...], JobOperationError | None]:
    if from_node_key is not None:
        downstream = set(dependency_downstream(definition, from_node_key))
        if not any(node == from_node_key or node in downstream for node in failed_nodes):
            return (), JobOperationError(
                job_id,
                "rerun",
                "skipped",
                node_key=None,
                reason_code="no_matching_failure",
                message=f"No latest failed run at or downstream of node {from_node_key!r}",
            )
        return (from_node_key,), None

    parents = dependency_parents(definition)
    targets: list[str] = []
    for node_key in failed_nodes:
        declared_node = definition.nodes.get(node_key)
        resolved_strategy = (
            (
                resolve_node_rerun_strategy(declared_node, category)
                if declared_node is not None
                else DEFAULT_ON_FAILURE.get(category, "rerun_self")
            )
            if strategy == "auto"
            else strategy
        )
        resolved = parents.get(node_key, []) if resolved_strategy == "rerun_upstream" else []
        resolved = [
            parent for parent in resolved if definition.nodes[parent].node_type != START_NODE_TYPE
        ]
        for target in resolved or [node_key]:
            if target not in targets:
                targets.append(target)
    return tuple(targets), None


def plan_failure_rerun(
    definition: Any,
    failed_nodes: list[str],
    nodes: list[dict[str, Any]],
    busy_pairs: set[tuple[str, str]],
    job_id: str,
    category: str,
    strategy: str,
    from_node_key: str | None,
) -> FailureRerunPlan:
    targets, error = resolve_failure_rerun_targets(
        definition, failed_nodes, category, strategy, from_node_key, job_id
    )
    if error is not None:
        return FailureRerunPlan(error=error)
    entries: list[str | JobOperationResult] = []
    for target in targets:
        ineligible = rerun_ineligible_from_nodes(definition, nodes, busy_pairs, job_id, target)
        entries.append(ineligible.to_result() if ineligible is not None else target)
    return FailureRerunPlan(targets=targets, entries=tuple(entries))
