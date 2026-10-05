"""Draft publish gates: node resolvability + secret-field intake guard.

Split from ``workflow_drafts`` (at its budget ceiling, AGENTS.md §5 拆分
纪律): that module keeps the YAML/definition helpers; this one owns the
per-node publish validation — ``type: agent`` nodes must resolve to exactly
one published Agent, ``type: code`` nodes must have published workspace
node code, and (#432) secret field values under a node's ``config:`` are
rejected fail-fast at publish: the YAML source editor has no vault
channel, and surfacing the error only at the first job's intake would
strand a published revision no new job can use (VAULT-SECRET-001).
"""

from __future__ import annotations

from server.app.jobs import JobQueries
from server.app.services.agent_node_profile import (
    build_capability_index,
    resolve_agent_node_profile,
)
from server.app.services.agent_node_profile_catalog import legacy_agent_catalog
from server.app.services.node_code_resolution import resolve_dispatch_node_code
from server.app.services.node_config import workflow_node_config_schemas
from server.app.services.node_config_secret_guard import secret_gate_errors
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.workflow_node_skill import node_skill_publish_error


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
    #432: secret field values under a node's ``config:`` fail here too
    (``secret_gate_errors``) — the YAML editor has no vault channel.
    """
    errors: list[str] = []
    agents = legacy_agent_catalog(job_db, workspace_id)
    schemas = workflow_node_config_schemas(definition, agents)
    errors.extend(secret_gate_errors(schemas, definition))
    index = build_capability_index(agents)
    for node in definition.executable_nodes.values():
        if node.node_type == "approval":
            continue
        is_agent = node.node_type == "agent"
        # #932：agent 节点经执行档案门面解析（P1 来源 = 恰好一个 published Agent）。
        profile = resolve_agent_node_profile(node, agents, index=index)
        if is_agent and profile is None:
            errors.append(
                f"Agent capability {node.capability} must resolve to exactly one published Agent"
            )
        # code 节点传 None：skill 绑定无意义，声明即拒绝；agent 节点的兜底取
        # 执行档案的 legacy skill（节点绑定优先）。
        agent_skill = profile.skill if profile is not None else None
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
