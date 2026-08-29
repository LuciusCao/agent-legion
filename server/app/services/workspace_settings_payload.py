"""Build the settings payload dict for a workspace record."""

from __future__ import annotations

from typing import Any


def workspace_settings_payload(workspace: dict[str, Any]) -> dict[str, Any]:
    """Build the settings payload dict for a workspace record.

    Node config secret fields are masked by
    ``workspace_settings_payload_with_schemas`` which owns the capability
    schemas (VAULT-SECRET-001).
    """
    workflow_key = str(workspace.get("default_workflow_key") or "")
    node_config = workspace.get("node_config")
    if not isinstance(node_config, dict):
        node_config = {}
    node_overrides = node_config.get(workflow_key)
    if not isinstance(node_overrides, dict):
        node_overrides = {}
    preview_config = workspace.get("preview_config")
    if not isinstance(preview_config, dict):
        preview_config = {}
    hidden = preview_config.get("hidden")
    return {
        "entityType": str(workspace.get("default_entity") or "question"),
        "workflowKey": workflow_key,
        "nodeConfig": node_overrides,
        "previewHidden": hidden if isinstance(hidden, list) else [],
    }
