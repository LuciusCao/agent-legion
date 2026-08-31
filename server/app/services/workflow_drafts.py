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
from server.app.workflows.workflow_node_skill import node_skill_publish_error


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
    """Publish validation, driven by each node's explicit ``type``.

    ``type: agent`` nodes must resolve to exactly one published Agent for
    their capability. ``type: code`` nodes (the implicit code pool, P-0.5)
    must have resolvable published workspace node code (EXEC-CODE-002) — a
    published Agent sharing the capability is simply unused, not an error.
    Start nodes carry no capability and never execute
    (EXEC-WORKFLOW-START-001), and approval gates never dispatch — the
    worker parks them for a human decision (EXEC-APPROVAL-001) — so both
    skip the checks; the #76 skill gate lives in ``node_skill_publish_error``.
    """
    errors: list[str] = []
    agents_by_capability: dict[str, list] = {}
    for agent_definition in published_agent_definitions(job_db, workspace_id).values():
        agents_by_capability.setdefault(agent_definition.capability, []).append(agent_definition)
    for node in definition.executable_nodes.values():
        if node.node_type == "approval":
            continue
        is_agent = node.node_type == "agent"
        candidates = agents_by_capability.get(node.capability, [])
        if is_agent and len(candidates) != 1:
            errors.append(
                f"Agent capability {node.capability} must resolve to exactly one published Agent"
            )
        # code 节点传 None：skill 绑定无意义，声明即拒绝；agent 节点的兜底取
        # 恰好一个 published Agent 的 skill（节点绑定优先）。
        agent_skill = candidates[0].skill if is_agent and len(candidates) == 1 else None
        skill_error = node_skill_publish_error(node, agent_skill)
        if skill_error is not None:
            errors.append(skill_error)
        if is_agent:
            continue
        node_code = resolve_dispatch_node_code(
            job_db,
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
