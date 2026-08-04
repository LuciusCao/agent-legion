from __future__ import annotations

from typing import Any

from server.app.workflows.definition import WorkflowDefinition


class ExecutionControlError(ValueError):
    """Raised when execution control state is invalid for a workflow definition."""


def ancestor_closure(definition: WorkflowDefinition, target_node_key: str) -> frozenset[str]:
    """Return all ancestors of *target_node_key* including itself.

    Raises ExecutionControlError when the target is unknown.
    """
    if target_node_key not in definition.nodes:
        raise ExecutionControlError(
            f"Unknown target node {target_node_key!r} in workflow {definition.key!r}"
        )

    closure: set[str] = {target_node_key}
    incoming: dict[str, list[str]] = {key: [] for key in definition.nodes}
    for edge in definition.edges:
        incoming[edge.target].append(edge.source)

    stack = [target_node_key]
    while stack:
        key = stack.pop()
        for dep in incoming[key]:
            if dep not in closure:
                closure.add(dep)
                stack.append(dep)
    return frozenset(closure)


def allowed_nodes(
    definition: WorkflowDefinition, execution_control: dict[str, Any]
) -> frozenset[str]:
    """Return the set of node keys that may be claimed for *execution_control*.

    *execution_control* must contain at least ``execution_mode``. For
    ``until_node`` mode it must also contain ``target_node_key``.

    Raises ExecutionControlError for unknown targets or invalid modes.
    """
    mode = execution_control.get("execution_mode", "full")
    if mode == "full":
        return frozenset(definition.nodes)
    if mode == "until_node":
        target = execution_control.get("target_node_key")
        if not target:
            raise ExecutionControlError("target_node_key is required for until_node execution mode")
        return ancestor_closure(definition, target)
    raise ExecutionControlError(f"Invalid execution_mode: {mode!r}")
