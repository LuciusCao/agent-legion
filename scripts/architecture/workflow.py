from pathlib import Path
from typing import Any

from server.app.workflows.builtin import BUILTIN_WORKFLOW_DEFINITIONS


def check_workflow_raw_definitions(definitions: dict[str, Any]) -> list[str]:
    """Lint raw built-in workflow definition mappings.

    Built-in DAGs live in ``server.app.workflows.builtin``; the same forbidden
    field rules that guarded the retired yaml files apply to these constants.
    """
    errors: list[str] = []
    for workflow_key, raw in sorted(definitions.items()):
        source = f"builtin workflow {workflow_key!r}"
        if not isinstance(raw, dict):
            errors.append(f"{source}: workflow definition must be a mapping")
            continue
        if "concurrency" in raw:
            errors.append(
                f"{source}: top-level 'concurrency' was removed; "
                "configure Executor limits at Workspace level"
            )
        nodes = raw.get("nodes")
        if not isinstance(nodes, dict):
            errors.append(f"{source}: workflow nodes must be a mapping")
            continue
        for node_key, node in nodes.items():
            if not isinstance(node, dict):
                errors.append(f"{source}: node {node_key} must be a mapping")
                continue
            if "runner" in node:
                errors.append(
                    f"{source}: node {node_key}: field 'runner' was removed; "
                    "bind a compatible Executor in Workspace settings"
                )
            if "agent" in node:
                errors.append(
                    f"{source}: node {node_key}: field 'agent' was removed; "
                    "invocation details belong to Executor capabilities"
                )
            capability = node.get("capability", "")
            if not isinstance(capability, str) or not capability:
                errors.append(f"{source}: node {node_key} must declare a non-empty capability")
    return errors


def check_workflow_definitions(root: Path) -> list[str]:
    del root  # Built-in definitions are code constants, not repo yaml files.
    return check_workflow_raw_definitions(BUILTIN_WORKFLOW_DEFINITIONS)
