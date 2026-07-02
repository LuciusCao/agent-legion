from __future__ import annotations

from typing import Any

import yaml

from server.app.jobs import JobQueries
from server.app.workflows.definition import (
    WorkflowDefinition,
    WorkflowDefinitionError,
    workflow_definition_from_mapping,
)


def workflow_definition_from_yaml_string(raw_yaml: str) -> WorkflowDefinition:
    raw = yaml.safe_load(raw_yaml)
    if not isinstance(raw, dict):
        raise WorkflowDefinitionError("Workflow definition must be a mapping")
    return workflow_definition_from_mapping(raw)


def validate_workflow_definition(raw_yaml: str) -> list[str]:
    try:
        workflow_definition_from_yaml_string(raw_yaml)
    except WorkflowDefinitionError as exc:
        return [str(exc)]
    return []


def validate_workflow_for_publish(
    *,
    definition: WorkflowDefinition,
    workspace_id: str,
    job_db: JobQueries,
    settings_executor_definitions: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    configuration = job_db.get_workspace_executor_configuration(workspace_id)
    bindings = {
        (binding["workflow_key"], binding["node_key"]): binding["executor_id"]
        for binding in configuration.get("bindings", [])
    }
    allocated = {allocation["executor_id"] for allocation in configuration.get("allocations", [])}
    for node in definition.nodes.values():
        executor_id = bindings.get((definition.key, node.key))
        if not executor_id:
            errors.append(f"missing executor binding for {definition.key}.{node.key}")
            continue
        if executor_id not in allocated:
            errors.append(f"executor {executor_id} is not allocated to workspace {workspace_id}")
            continue
        executor = settings_executor_definitions.get(executor_id)
        capabilities = getattr(executor, "capabilities", [])
        if node.capability not in capabilities:
            errors.append(f"executor {executor_id} does not support capability {node.capability}")
    return errors
