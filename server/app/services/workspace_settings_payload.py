"""Build the settings payload dict for a workspace record."""

from __future__ import annotations

from typing import Any

from server.app.services.vault import mask_resource_secrets
from server.app.workflows.resource_schemas import resource_schemas_payload
from server.app.workflows.resources import RESOURCE_PROVIDERS

_RESOURCE_SCHEMAS = resource_schemas_payload(RESOURCE_PROVIDERS)


def workspace_settings_payload(workspace: dict[str, Any]) -> dict[str, Any]:
    """Build the settings payload dict for a workspace record."""
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
        "resources": mask_resource_secrets(resources),
        "resourceSchemas": _RESOURCE_SCHEMAS,
        "nodeConfig": node_overrides,
    }
