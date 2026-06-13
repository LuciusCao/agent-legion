from __future__ import annotations

from typing import Any

from server.app.pipelines.definition import PipelineDefinition


class ExecutionControlError(ValueError):
    """Raised when execution control state is invalid for a pipeline definition."""


def ancestor_closure(definition: PipelineDefinition, target_node_key: str) -> frozenset[str]:
    """Return all ancestors of *target_node_key* including itself.

    Raises ExecutionControlError when the target is unknown.
    """
    if target_node_key not in definition.nodes:
        raise ExecutionControlError(
            f"Unknown target node {target_node_key!r} in pipeline {definition.key!r}"
        )

    closure: set[str] = {target_node_key}
    stack = [target_node_key]
    while stack:
        key = stack.pop()
        for dep in definition.nodes[key].after:
            if dep not in closure:
                closure.add(dep)
                stack.append(dep)
    return frozenset(closure)


def allowed_nodes(
    definition: PipelineDefinition, execution_control: dict[str, Any]
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
