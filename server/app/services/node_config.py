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
(CONFIG-RUNTIME-MUTABLE-001, ``node_config_runtime``); ``timeout_seconds``
follows CONFIG-RUNTIME-TIMEOUT-001 (``runtime_reserved_config``), while
``sandbox_network`` stays frozen.
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
from server.app.services.node_config_runtime import runtime_mutable_keys
from server.app.services.node_config_secret_guard import reject_secret_violations
from server.app.services.node_execution_config import (
    agent_effective_schema,
    merge_reserved_execution_schema,
)
from server.app.services.node_secrets import strip_secret_fields
from server.app.services.runtime_reserved_config import (
    TIMEOUT_KEY,
    chain_override,
    dispatch_timeout,
)
from server.app.workflows.schema import WorkflowDefinition, WorkflowNode


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

    ``type: agent`` nodes keep their Agent Definition schema — with the
    platform-reserved execution keys merged UNDER it since #550 (an agent
    node's timeout is configurable like a code node's; ``sandbox_network``
    rides along inertly — the agent runtime ignores it). The merged default
    keeps the agent product constant (1800s), NOT the code-node 600 — the
    upgrade must not silently cut existing agent runs' budget. Every other
    node is code-routed and gets the same merge into its declared schema.
    The explicit node type decides (#284): a code node may share its
    capability with a published Agent without inheriting the Agent's schema.
    """
    if node.node_type == "agent":
        return agent_effective_schema(agent_schemas.get(node.capability, {}))
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


def resolve_node_config(
    config_schema: dict[str, Any],
    node_config: Mapping[str, Any],
    workspace_override: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge defaults → node config → workspace override, validating each layer.

    Secret fields are vault-managed markers; they bypass generic validation (VAULT-SECRET-001). #432: secret values in the node config layer must be the exact vault marker.
    """
    if not config_schema:
        if node_config or workspace_override:
            raise ConfigSchemaError("node declares config but its capability has no config_schema")
        return {}
    reject_secret_violations(config_schema, node_config, "node config")
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
        workspace_override = chain_override(overrides.get(node.key, {}))
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


def dispatch_config_resolution(
    config_schema: dict[str, Any],
    node: Any,
    workflow_key: str,
    workspace: Mapping[str, Any] | None,
    run_payload: Mapping[str, Any] | None,
    fallback_defaults: Mapping[str, Any] | None = None,
    *,
    decide: bool = True,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Effective config at dispatch time plus the ``timeout_seconds`` entry.

    The job's frozen config wins. Pre-mechanism jobs (or replays without a
    frozen config) fall back to live resolution from the node and workspace
    layers. Frozen snapshots predating the reserved execution keys get
    *fallback_defaults* underneath (frozen values always win), so in-flight
    old jobs keep their node-declared network behavior (P-0.5). Frozen
    snapshots are overlaid with a live re-resolution of the keys declared
    ``runtime_mutable: true`` (CONFIG-RUNTIME-MUTABLE-001); ``sandbox_network``
    and everything else stay frozen. ``timeout_seconds`` follows the #691
    model (``runtime_reserved_config``): ``decide=True`` is the local code
    pool's decision point (base + live L2); ``decide=False`` (remote enqueue)
    yields only the base the queued manifest carries for the claim to decide.
    The second element is that ``{"value", "source"}`` entry (None when the
    schema carries no reserved timeout).
    """
    frozen = frozen_node_config(run_payload, node.key)
    raw_override = workspace_node_overrides(workspace, workflow_key).get(node.key, {})
    override = chain_override(raw_override)
    if frozen is None:
        effective = resolve_node_config(config_schema, node.config, override)
    else:
        effective = {**fallback_defaults, **frozen} if fallback_defaults else dict(frozen)
        mutable = runtime_mutable_keys(config_schema)
        if mutable:
            live = resolve_node_config(config_schema, node.config, override)
            effective.update({key: live[key] for key in mutable if key in live})
    entry = dispatch_timeout(config_schema, node, raw_override, workspace, decide=decide)
    if entry is not None:
        effective[TIMEOUT_KEY] = entry["value"]
    return effective, entry


def dispatch_effective_config(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """``dispatch_config_resolution`` without the timeout entry."""
    return dispatch_config_resolution(*args, **kwargs)[0]
