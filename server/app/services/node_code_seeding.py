"""Seed-if-absent helper for workspace-scoped node code."""

from __future__ import annotations

from server.app.services.job_errors import ConflictError, NotFoundError
from server.app.services.node_codes import NodeCodeService


def seed_workspace_node_code(
    service: NodeCodeService,
    workspace_id: str,
    workflow_key: str,
    node_key: str,
    code: str,
    change_note: str,
) -> bool:
    """Publish factory code into one workspace when that entity is absent."""
    if service.list_versions(workspace_id, workflow_key, node_key):
        return False
    try:
        service.save_draft(
            workspace_id,
            workflow_key,
            node_key,
            code,
            "system",
            change_note,
        )
        service.publish(workspace_id, workflow_key, node_key)
    except (ConflictError, NotFoundError):
        # Concurrent seed: another process won the immutable-version race.
        return False
    return True
