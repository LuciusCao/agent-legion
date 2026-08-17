"""Workspace-level per-node config override updates (spec D8).

Split from ``workspace_configuration.py`` to keep that module within its size
budget. Overrides are stored in ``workspaces.node_config_json`` keyed by
workflow then node, validated against the capability's config_schema, and
secret fields are diverted into the workspace vault (VAULT-SECRET-001).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from server.app.agent_catalog import AgentDefinition
from server.app.config_schema import ConfigSchemaError, validate_config_values
from server.app.jobs import JobQueries
from server.app.services.job_errors import InvalidOperationError
from server.app.services.node_config import workflow_node_config_schemas
from server.app.services.node_secrets import apply_node_secret_fields, strip_secret_fields
from server.app.services.vault import VaultError, VaultService
from server.app.services.workflow_catalog import WorkflowCatalogService


def update_workspace_node_config(
    job_db: JobQueries,
    workflows: WorkflowCatalogService,
    agent_definitions: Mapping[str, AgentDefinition],
    workspace: dict[str, Any],
    patch: dict[str, Any],
) -> dict[str, Any]:
    """Validate and persist per-node overrides for the workspace's workflow.

    An empty mapping for a node clears that node's workspace override.
    """
    workspace_id = str(workspace["id"])
    raw = patch.get("nodeConfig")
    if not isinstance(raw, dict):
        raise InvalidOperationError("nodeConfig must be a mapping of node key to values")
    workflow_key = str(workspace.get("default_workflow_key") or "")
    definition = workflows.definition(workflow_key)
    schemas = workflow_node_config_schemas(definition, agent_definitions)
    vault = VaultService(job_db.path, workflows.settings.config)
    node_config = workspace.get("node_config")
    next_node_config = dict(node_config) if isinstance(node_config, dict) else {}
    workflow_overrides = next_node_config.get(workflow_key)
    workflow_overrides = dict(workflow_overrides) if isinstance(workflow_overrides, dict) else {}
    for node_key, values in raw.items():
        if node_key not in definition.nodes:
            raise InvalidOperationError(f"Unknown node {node_key!r} for workflow {workflow_key}")
        node_schema = schemas.get(node_key)
        if not node_schema:
            raise InvalidOperationError(
                f"Node {node_key!r} does not declare configurable parameters"
            )
        if not isinstance(values, dict):
            raise InvalidOperationError(f"nodeConfig.{node_key} must be a mapping")
        try:
            # Secret fields are validated/persisted by the vault chain, not by
            # the generic schema validation (VAULT-SECRET-001).
            validate_config_values(
                node_schema,
                strip_secret_fields(node_schema, values),
                partial=True,
                path=f"nodeConfig.{node_key}",
            )
            stored = apply_node_secret_fields(
                vault,
                workspace_id,
                workflow_key,
                node_key,
                node_schema,
                values,
                workflow_overrides.get(node_key, {}),
            )
        except (ConfigSchemaError, VaultError) as exc:
            raise InvalidOperationError(str(exc)) from exc
        if stored:
            workflow_overrides[node_key] = stored
        else:
            workflow_overrides.pop(node_key, None)
    next_node_config[workflow_key] = workflow_overrides
    return job_db.update_workspace(workspace_id, node_config=next_node_config)
