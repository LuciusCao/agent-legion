"""Resolve which instance-level external connection a workspace node uses."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from server.app.services.node_config import (
    executor_capability_config_schemas,
    workspace_node_overrides,
)


def workspace_node_connection_key(
    executor_definitions: Mapping[str, Any],
    workspace: Mapping[str, Any] | None,
    workflow_key: str,
    node_key: str,
    capability: str,
) -> str:
    """Workspace node override first, then the capability config_schema default."""
    override = workspace_node_overrides(workspace, workflow_key).get(node_key, {})
    key = str(override.get("connection") or "").strip()
    if key:
        return key
    schema = executor_capability_config_schemas(executor_definitions).get(capability) or {}
    properties = schema.get("properties") if isinstance(schema, Mapping) else None
    prop = properties.get("connection") if isinstance(properties, Mapping) else None
    if isinstance(prop, Mapping):
        return str(prop.get("default") or "").strip()
    return ""
