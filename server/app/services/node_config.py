"""Resolve effective per-node capability config (spec D8, P-0.5).

Chain: config_schema defaults → workflow node ``config`` → workspace override
(``workspaces.node_config_json`` keyed by workflow then node). Schema source
priority: Agent Definition → node-declared ``config_schema`` (the executor
capability fallback retired with the executor concept, schema v47);
code-routed nodes also get the platform-reserved execution keys merged in
(``node_execution_config``). The resolved map is frozen into the intake
batch payload; dispatch reads the frozen value and only forwards
schema-whitelisted, non-secret keys (CONFIG-MANIFEST-001). Keys declared
``runtime_mutable: true`` are overlaid with a live re-resolution at dispatch
(CONFIG-RUNTIME-MUTABLE-001, ``node_config_runtime``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from server.app.agent_catalog import AgentDefinition
from server.app.config_schema import (
    ConfigSchemaError,
    config_schema_defaults,
    validate_config_values,
)
from server.app.services.node_config_batch import frozen_node_config
from server.app.services.node_config_runtime import runtime_mutable_keys
from server.app.services.node_execution_config import merge_reserved_execution_schema
from server.app.services.node_secrets import secret_config_fields, strip_secret_fields
from server.app.workflows.schema import WorkflowDefinition, WorkflowNode

if TYPE_CHECKING:
    from server.app.jobs import JobQueries


def _agent_schemas(
    agent_definitions: Mapping[str, AgentDefinition],
) -> dict[str, dict[str, Any]]:
    return {d.capability: d.config_schema for d in agent_definitions.values() if d.config_schema}


def capability_config_schemas(
    agent_definitions: Mapping[str, AgentDefinition],
    workflow: WorkflowDefinition | None = None,
) -> dict[str, dict[str, Any]]:
    """Map capability → declared config_schema.

    Agent Definitions win, then node-declared schemas (when *workflow* is
    given); the executor fallback retired in P-0.5 step 3.
    """
    schemas = _agent_schemas(agent_definitions)
    if workflow is not None:
        for node in workflow.nodes.values():
            if node.config_schema:
                schemas.setdefault(node.capability, dict(node.config_schema))
    return schemas


def _node_config_schema(
    node: WorkflowNode,
    agent_schemas: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    """One node's effective schema: Agent Definition → node-declared.

    ``type: agent`` nodes keep their Agent Definition schema untouched.
    Every other node is code-routed and gets the platform-reserved
    execution keys merged into its declared schema. The explicit node type
    decides (#284): a code node may share its capability with a published
    Agent without inheriting the Agent's schema.
    """
    if node.node_type == "agent":
        return agent_schemas.get(node.capability, {})
    return merge_reserved_execution_schema(node.config_schema)


def workflow_node_config_schemas(
    definition: WorkflowDefinition,
    agent_definitions: Mapping[str, AgentDefinition],
) -> dict[str, dict[str, Any]]:
    """Map node key → effective config_schema (reserved keys merged for code nodes)."""
    agent_schemas = _agent_schemas(agent_definitions)
    schemas: dict[str, dict[str, Any]] = {}
    for node in definition.executable_nodes.values():
        schema = _node_config_schema(node, agent_schemas)
        # Approval gates never dispatch (EXEC-APPROVAL-001): no config surface.
        if schema and node.node_type != "approval":
            schemas[node.key] = schema
    return schemas


def workspace_node_overrides(
    workspace: Mapping[str, Any] | None,
    workflow_key: str,
) -> dict[str, dict[str, Any]]:
    """Extract the workspace's per-node overrides for one workflow."""
    if not isinstance(workspace, Mapping):
        return {}
    node_config = workspace.get("node_config")
    if not isinstance(node_config, Mapping):
        return {}
    workflow_overrides = node_config.get(workflow_key)
    if not isinstance(workflow_overrides, Mapping):
        return {}
    return {
        str(node_key): dict(values)
        for node_key, values in workflow_overrides.items()
        if isinstance(values, Mapping)
    }


def prune_workspace_node_overrides(
    job_db: JobQueries,
    workspace_id: str,
    definition: WorkflowDefinition,
    agent_definitions: Mapping[str, AgentDefinition],
) -> bool:
    """Strip override entries the (newly published) revision no longer accepts.

    Publish only validates the schema, never the stored workspace overrides
    (#428 二轮复审 P2-1): a renamed/removed schema property (or a dropped
    node) leaves stale keys in ``workspaces.node_config_json``. The very next
    intake would then fail ``resolve_workflow_node_configs`` →
    ``validate_config_values`` whitelist validation for every new job, and
    the override card's PATCH-everything save would 400 on unknown keys —
    with 「清除覆盖」 as the only exit, discarding legitimate overrides too.

    Stale *keys* (not in the node's effective schema properties) are pruned;
    values that no longer match the property type are pruned as well (the
    intake type check raises on them the same way). Secret fields count as
    valid keys (their stored ``{"secret_ref": ...}`` markers bypass value
    validation). Overrides of nodes the new revision dropped are left alone —
    resolve skips them, and the settings PATCH already rejects them. Returns
    True when anything was pruned.
    """
    workspace = job_db.get_workspace(workspace_id)
    overrides = workspace_node_overrides(workspace, definition.key)
    if workspace is None or not overrides:
        return False
    schemas = workflow_node_config_schemas(definition, agent_definitions)
    pruned = False
    for node_key in list(overrides):
        values = overrides[node_key]
        if node_key not in schemas:
            continue  # unknown node: PATCH rejects it, but keep hands off here
        cleaned = _prune_stale_override_keys(values, schemas[node_key].get("properties") or {})
        if cleaned == values:
            continue
        pruned = True
        if cleaned:
            overrides[node_key] = cleaned
        else:
            overrides.pop(node_key, None)
    if not pruned:
        return False
    node_config = dict(workspace.get("node_config") or {})
    node_config[definition.key] = overrides
    job_db.update_workspace(workspace_id, node_config=node_config)
    return True


def _value_is_type(prop: Mapping[str, Any], value: Any) -> bool:
    """Type check mirroring ``config_schema._type_matches``; non-scalars pass.

    Vault ``{"secret_ref": ...}`` markers only occur on secret fields, which
    the pruner already treats as key-valid without a value check; other
    non-scalar shapes stay for intake validation to report rather than being
    silently pruned.
    """
    expected = prop.get("type")
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def _prune_stale_override_keys(
    values: Mapping[str, Any],
    properties: Mapping[str, Any],
) -> dict[str, Any]:
    """Copy of one node's override without stale keys (see the prune docstring).

    Secret fields store ``{"secret_ref": ...}`` vault markers rather than the
    declared scalar type, so key validity is enough for them.
    """
    secret_fields = secret_config_fields(dict(properties=properties))
    return {
        key: value
        for key, value in values.items()
        if key in properties and (key in secret_fields or _value_is_type(properties[key], value))
    }


def resolve_node_config(
    config_schema: dict[str, Any],
    node_config: Mapping[str, Any],
    workspace_override: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge defaults → node config → workspace override, validating each layer.

    Secret fields are vault-managed markers; they bypass validation (VAULT-SECRET-001).
    """
    if not config_schema:
        if node_config or workspace_override:
            raise ConfigSchemaError("node declares config but its capability has no config_schema")
        return {}
    plain_node = strip_secret_fields(config_schema, dict(node_config))
    plain_override = strip_secret_fields(config_schema, dict(workspace_override))
    validate_config_values(config_schema, plain_node, partial=True, path="node config")
    validate_config_values(
        config_schema, plain_override, partial=True, path="workspace node config"
    )
    effective = config_schema_defaults(config_schema)
    effective.update(plain_node)
    effective.update(plain_override)
    validated = validate_config_values(config_schema, effective)
    for key, value in {**node_config, **workspace_override}.items():
        if key not in plain_node and key not in plain_override:
            validated[key] = value
    return validated


def resolve_workflow_node_configs(
    definition: WorkflowDefinition,
    agent_definitions: Mapping[str, AgentDefinition],
    workspace: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Resolve the effective config of every node for an intake freeze."""
    agent_schemas = _agent_schemas(agent_definitions)
    overrides = workspace_node_overrides(workspace, definition.key)
    resolved: dict[str, dict[str, Any]] = {}
    for node in definition.executable_nodes.values():
        node_schema = _node_config_schema(node, agent_schemas)
        workspace_override = overrides.get(node.key, {})
        # Approval gates never dispatch (EXEC-APPROVAL-001): their config
        # (rework_target/feedback_artifact) is platform semantics consumed by
        # the approval service, not an execution config to validate/freeze.
        if node.node_type == "approval" or (
            not node_schema and not node.config and not workspace_override
        ):
            continue
        try:
            resolved[node.key] = resolve_node_config(node_schema, node.config, workspace_override)
        except ConfigSchemaError as exc:
            raise ConfigSchemaError(f"node {node.key!r}: {exc}") from exc
    return resolved


def dispatch_effective_config(
    config_schema: dict[str, Any],
    node: Any,
    workflow_key: str,
    workspace: Mapping[str, Any] | None,
    run_payload: Mapping[str, Any] | None,
    fallback_defaults: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Effective config at dispatch time: the job's frozen config wins.

    Jobs intaken before this mechanism existed (or replayed without a frozen
    config) fall back to live resolution from the node and workspace layers.
    Frozen snapshots predating the reserved execution keys get
    *fallback_defaults* underneath (frozen values always win), so in-flight
    old jobs keep their node-declared timeout/network behavior (P-0.5).

    Frozen snapshots are overlaid with a live re-resolution of the keys
    declared ``runtime_mutable: true`` (CONFIG-RUNTIME-MUTABLE-001); everything
    else — including the platform-reserved execution keys — stays frozen.
    """
    frozen = frozen_node_config(run_payload, node.key)
    if frozen is None:
        overrides = workspace_node_overrides(workspace, workflow_key)
        return resolve_node_config(config_schema, node.config, overrides.get(node.key, {}))
    effective = {**fallback_defaults, **frozen} if fallback_defaults else dict(frozen)
    mutable = runtime_mutable_keys(config_schema)
    if not mutable:
        return effective
    overrides = workspace_node_overrides(workspace, workflow_key)
    live = resolve_node_config(config_schema, node.config, overrides.get(node.key, {}))
    return {**effective, **{key: live[key] for key in mutable if key in live}}
