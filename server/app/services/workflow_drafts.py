from __future__ import annotations

from typing import Any

import yaml

from server.app.jobs import JobQueries
from server.app.services.agent_service import published_agent_definitions
from server.app.workflows.definition import (
    WorkflowDefinition,
    WorkflowDefinitionError,
    workflow_definition_from_mapping,
)


def workflow_definition_from_yaml_string(raw_yaml: str) -> WorkflowDefinition:
    try:
        raw = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        raise WorkflowDefinitionError(f"Workflow definition is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise WorkflowDefinitionError("Workflow definition must be a mapping")
    return workflow_definition_from_mapping(raw)


def validate_workflow_definition(
    raw_yaml: str,
) -> list[str]:
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
    capability_counts: dict[str, int] = {}
    for agent_definition in published_agent_definitions(job_db.path).values():
        capability_counts[agent_definition.capability] = (
            capability_counts.get(agent_definition.capability, 0) + 1
        )
    for node in definition.nodes.values():
        count = capability_counts.get(node.capability, 0)
        if count > 0:
            if count != 1:
                errors.append(
                    f"Agent capability {node.capability} must resolve to exactly one published Agent"
                )
            continue
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
