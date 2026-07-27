"""Resolve effective per-node capability config (spec D8).

Chain: config_schema defaults → workflow node ``config`` → workspace override
(``workspaces.node_config_json`` keyed by workflow then node). The resolved
map is frozen into the intake batch payload; dispatch reads the frozen value
and only forwards schema-whitelisted, non-secret keys (CONFIG-MANIFEST-001).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from server.app.agent_catalog import AgentDefinition
from server.app.config_schema import (
    ConfigSchemaError,
    config_schema_defaults,
    validate_config_values,
)
from server.app.workflows.schema import WorkflowDefinition


def capability_config_schemas(
    agent_definitions: Mapping[str, AgentDefinition],
) -> dict[str, dict[str, Any]]:
    """Map capability → declared config_schema (phase 1: one agent per capability)."""
    return {
        definition.capability: definition.config_schema
        for definition in agent_definitions.values()
        if definition.config_schema
    }


def workflow_node_config_schemas(
    definition: WorkflowDefinition,
    agent_definitions: Mapping[str, AgentDefinition],
) -> dict[str, dict[str, Any]]:
    """Map node key → config_schema for nodes whose capability declares one."""
    schemas = capability_config_schemas(agent_definitions)
    return {
        node.key: schemas[node.capability]
        for node in definition.nodes.values()
        if node.capability in schemas
    }


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
    """Merge defaults → node config → workspace override, validating each layer."""
    if not config_schema:
        if node_config or workspace_override:
            raise ConfigSchemaError("node declares config but its capability has no config_schema")
        return {}
    validate_config_values(config_schema, dict(node_config), partial=True, path="node config")
    validate_config_values(
        config_schema, dict(workspace_override), partial=True, path="workspace node config"
    )
    effective = config_schema_defaults(config_schema)
    effective.update(node_config)
    effective.update(workspace_override)
    return validate_config_values(config_schema, effective)


def resolve_workflow_node_configs(
    definition: WorkflowDefinition,
    agent_definitions: Mapping[str, AgentDefinition],
    workspace: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Resolve the effective config of every node for an intake freeze."""
    schemas = capability_config_schemas(agent_definitions)
    overrides = workspace_node_overrides(workspace, definition.key)
    resolved: dict[str, dict[str, Any]] = {}
    for node in definition.nodes.values():
        node_schema = schemas.get(node.capability, {})
        workspace_override = overrides.get(node.key, {})
        if not node_schema and not node.config and not workspace_override:
            continue
        try:
            resolved[node.key] = resolve_node_config(node_schema, node.config, workspace_override)
        except ConfigSchemaError as exc:
            raise ConfigSchemaError(f"node {node.key!r}: {exc}") from exc
    return resolved


def frozen_node_config(
    batch_payload: Mapping[str, Any] | None,
    node_key: str,
) -> dict[str, Any] | None:
    """Read one node's frozen config from an intake batch payload, if present."""
    if not isinstance(batch_payload, Mapping):
        return None
    node_config = batch_payload.get("node_config")
    if not isinstance(node_config, Mapping):
        return None
    values = node_config.get(node_key)
    return dict(values) if isinstance(values, Mapping) else None


def batch_source_payload(job_db: Any, job: Mapping[str, Any]) -> dict[str, Any] | None:
    """Decode the source payload of the job's intake batch, if available."""
    batch_id = job.get("batch_id")
    if not batch_id:
        return None
    batch = job_db.get_batch(str(batch_id))
    if not batch:
        return None
    try:
        payload = json.loads(str(batch.get("source_payload_json") or ""))
    except (TypeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def dispatch_effective_config(
    config_schema: dict[str, Any],
    node: Any,
    workflow_key: str,
    workspace: Mapping[str, Any] | None,
    batch_payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Effective config at dispatch time: frozen intake snapshot wins.

    Jobs intaken before this mechanism existed (or replayed without a batch)
    fall back to live resolution from the node and workspace layers.
    """
    frozen = frozen_node_config(batch_payload, node.key)
    if frozen is not None:
        return frozen
    overrides = workspace_node_overrides(workspace, workflow_key)
    return resolve_node_config(config_schema, node.config, overrides.get(node.key, {}))
