from __future__ import annotations

import yaml

from server.app.jobs import JobQueries
from server.app.services.agent_service import published_agent_definitions
from server.app.services.node_code_resolution import resolve_dispatch_node_code
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
    custom_nodes_enabled: bool,
) -> list[str]:
    """Publish validation: Agent routing uniqueness + code resolvability.

    P-0.5: non-Agent-routed nodes all run on the implicit code pool, so the
    publish gate is "resolvable published workspace node code", not executor
    binding/allocation checks. Start nodes carry no capability and never
    execute (EXEC-WORKFLOW-START-001), so they skip both checks.
    """
    errors: list[str] = []
    capability_counts: dict[str, int] = {}
    for agent_definition in published_agent_definitions(job_db.path, workspace_id).values():
        capability_counts[agent_definition.capability] = (
            capability_counts.get(agent_definition.capability, 0) + 1
        )
    for node in definition.executable_nodes.values():
        count = capability_counts.get(node.capability, 0)
        if count > 0:
            if count != 1:
                errors.append(
                    f"Agent capability {node.capability} must resolve to exactly one published Agent"
                )
            continue
        node_code = resolve_dispatch_node_code(
            job_db.path,
            custom_nodes_enabled,
            workspace_id,
            definition.key,
            node.key,
            None,
        )
        if node_code is None:
            errors.append(
                f"no published node code for {definition.key}.{node.key} "
                "(publish a workspace version first, EXEC-CODE-002)"
            )
    return errors
