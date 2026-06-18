from __future__ import annotations

from typing import Any


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
    return {
        "entityType": str(workspace.get("default_entity") or "question"),
        "intakeModes": enabled_modes if isinstance(enabled_modes, list) else [],
        "labelOverrides": label_overrides if isinstance(label_overrides, dict) else {},
        "workflowKey": str(workspace.get("default_workflow_key") or "question_content"),
        "resources": resources,
    }
