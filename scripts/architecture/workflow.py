from pathlib import Path
from typing import Any

from server.app.workflows.builtin import BUILTIN_WORKFLOW_DEFINITIONS
from server.app.workflows.loader import load_workflow_nodes_with_execution
from server.app.workflows.schema import WorkflowDefinitionError


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
        previous_error_count = len(errors)
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
        if len(errors) == previous_error_count:
            try:
                load_workflow_nodes_with_execution(raw, nodes)
            except WorkflowDefinitionError as exc:
                errors.append(f"{source}: {exc}")
    return errors


def check_workflow_definitions(root: Path) -> list[str]:
    del root  # Built-in definitions are code constants, not repo yaml files.
    return check_workflow_raw_definitions(BUILTIN_WORKFLOW_DEFINITIONS)
