"""Resolve effective per-node capability config (spec D8, P-0.5).

Chain: config_schema defaults → workflow node ``config`` → workspace override
(``workspaces.node_config_json`` keyed by workflow then node). Schema source
priority: Agent Definition → node-declared ``config_schema`` (the executor
capability fallback retired with the executor concept, schema v47);
code-routed nodes also get the platform-reserved execution keys merged in
(``node_execution_config``). The resolved map is frozen into the intake
batch payload; dispatch reads the frozen value and only forwards
schema-whitelisted, non-secret keys (CONFIG-MANIFEST-001).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from server.app.agent_catalog import AgentDefinition
from server.app.config_schema import (
    ConfigSchemaError,
    config_schema_defaults,
    validate_config_values,
)
from server.app.services.node_config_batch import frozen_node_config
from server.app.services.node_execution_config import merge_reserved_execution_schema
from server.app.services.node_secrets import strip_secret_fields
from server.app.workflows.schema import WorkflowDefinition, WorkflowNode


def _agent_schemas(
    agent_definitions: Mapping[str, AgentDefinition],
) -> dict[str, dict[str, Any]]:
    return {
        definition.capability: definition.config_schema
        for definition in agent_definitions.values()
        if definition.config_schema
    }


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
    agent_capabilities: set[str],
) -> dict[str, Any]:
    """One node's effective schema: Agent Definition → node-declared.

    Agent-routed nodes keep their Agent Definition schema untouched. Every
    other node is code-routed (P-0.5) and gets the platform-reserved
    execution keys merged into its declared schema.
    """
    if node.capability in agent_capabilities:
        return agent_schemas.get(node.capability, {})
    return merge_reserved_execution_schema(node.config_schema)


def workflow_node_config_schemas(
    definition: WorkflowDefinition,
    agent_definitions: Mapping[str, AgentDefinition],
) -> dict[str, dict[str, Any]]:
    """Map node key → effective config_schema (reserved keys merged for code nodes)."""
    agent_schemas = _agent_schemas(agent_definitions)
    agent_capabilities = {d.capability for d in agent_definitions.values()}
    schemas: dict[str, dict[str, Any]] = {}
    for node in definition.nodes.values():
        schema = _node_config_schema(node, agent_schemas, agent_capabilities)
        if schema:
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
    agent_capabilities = {d.capability for d in agent_definitions.values()}
    overrides = workspace_node_overrides(workspace, definition.key)
    resolved: dict[str, dict[str, Any]] = {}
    for node in definition.nodes.values():
        node_schema = _node_config_schema(node, agent_schemas, agent_capabilities)
        workspace_override = overrides.get(node.key, {})
        if not node_schema and not node.config and not workspace_override:
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
    batch_payload: Mapping[str, Any] | None,
    fallback_defaults: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Effective config at dispatch time: frozen intake snapshot wins.

    Jobs intaken before this mechanism existed (or replayed without a batch)
    fall back to live resolution from the node and workspace layers. Frozen
    snapshots predating the reserved execution keys get *fallback_defaults*
    underneath (frozen values always win), so in-flight old jobs keep their
    node-declared timeout/network behavior (P-0.5).
    """
    frozen = frozen_node_config(batch_payload, node.key)
    if frozen is not None:
        if fallback_defaults:
            return {**fallback_defaults, **frozen}
        return frozen
    overrides = workspace_node_overrides(workspace, workflow_key)
    return resolve_node_config(config_schema, node.config, overrides.get(node.key, {}))
