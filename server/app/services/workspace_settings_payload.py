"""Build the settings payload dict for a workspace record."""

from __future__ import annotations

from typing import Any

from server.app.services.vault_resources import mask_resource_secrets
from server.app.workflows.resource_providers import ResourceProviderDeclarations
from server.app.workflows.resource_schemas import resource_schemas_payload


def workspace_settings_payload(
    workspace: dict[str, Any],
    *,
    declarations: ResourceProviderDeclarations,
) -> dict[str, Any]:
    """Build the settings payload dict for a workspace record.

    ``declarations`` is required (never defaulted to empty) so resource secret
    fields are always masked with the real provider schemas (VAULT-SECRET-001).
    """
    intake_config = workspace.get("intake_config")
    if not isinstance(intake_config, dict):
        intake_config = {}
    enabled_modes = intake_config.get("enabled_modes")
    label_overrides = intake_config.get("label_overrides")
    resource_config = workspace.get("resource_config")
    if not isinstance(resource_config, dict):
        resource_config = {}
    resources = resource_config.get("resources")
    if not isinstance(resources, dict):
        resources = {}
    workflow_key = str(workspace.get("default_workflow_key") or "")
    node_config = workspace.get("node_config")
    if not isinstance(node_config, dict):
        node_config = {}
    node_overrides = node_config.get(workflow_key)
    if not isinstance(node_overrides, dict):
        node_overrides = {}
    return {
        "entityType": str(workspace.get("default_entity") or "question"),
        "intakeModes": enabled_modes if isinstance(enabled_modes, list) else [],
        "labelOverrides": label_overrides if isinstance(label_overrides, dict) else {},
        "workflowKey": workflow_key,
        "resources": mask_resource_secrets(resources, declarations.schemas),
        "resourceSchemas": resource_schemas_payload(declarations.providers, declarations.schemas),
        "nodeConfig": node_overrides,
    }
