"""One-time migration of legacy global demo code into workspace scope."""

from __future__ import annotations

from typing import TYPE_CHECKING

from server.app.services.demo_node_seed import (
    DEMO_NODE_SOURCES,
    DEMO_WORKFLOW_KEY,
    seed_demo_workspace_node_codes,
)
from server.app.services.node_codes import NodeCodeService

if TYPE_CHECKING:
    from server.app.jobs import JobQueries
    from server.app.settings import Settings


def migrate_demo_node_codes_to_workspaces(settings: Settings, job_db: JobQueries) -> int:
    """Copy active legacy globals into bound workspaces, then archive them."""
    if not settings.executor_runtime.workflows.custom_nodes_enabled:
        return 0
    service = NodeCodeService(settings.database_url, custom_nodes_enabled=True)
    legacy_codes = {
        node_key: str(row["code"])
        for node_key, _relative in DEMO_NODE_SOURCES
        if (row := service.get_global_published(DEMO_WORKFLOW_KEY, node_key)) is not None
    }
    if not legacy_codes:
        # Steady-state startup never scans all workspaces.
        return 0

    seeded = 0
    for workspace in job_db.list_workspaces():
        if workspace.get("default_workflow_key") == DEMO_WORKFLOW_KEY:
            seeded += len(
                seed_demo_workspace_node_codes(
                    settings, str(workspace["id"]), legacy_codes=legacy_codes
                )
            )
    for node_key, _relative in DEMO_NODE_SOURCES:
        service.archive_all(None, DEMO_WORKFLOW_KEY, node_key)
    return seeded
