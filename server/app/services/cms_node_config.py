"""Server-side CMS config of a node (no frozen batch context)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from server.app.services.node_config import workspace_node_overrides


def cms_node_config(
    settings_config: Mapping[str, Any] | None,
    workspace: Mapping[str, Any] | None,
    workflow_key: str,
    node_key: str,
) -> dict[str, Any]:
    """Effective CMS config: global ``cms:`` defaults + workspace node override.

    Used by server-side consumers (question detail, test-connection). Secret
    values stay as ``{"secret_ref": ...}`` markers; callers resolve them via
    ``VaultService`` in memory (VAULT-SECRET-001).
    """
    cms = settings_config.get("cms") if isinstance(settings_config, Mapping) else None
    merged: dict[str, Any] = dict(cms) if isinstance(cms, Mapping) else {}
    override = workspace_node_overrides(workspace, workflow_key).get(node_key, {})
    merged.update({key: value for key, value in override.items() if value not in (None, "")})
    return merged
