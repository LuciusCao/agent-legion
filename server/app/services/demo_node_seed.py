"""Workspace-scoped factory seed for the demo workflow's two code nodes."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from server.app.services.node_code_seeding import seed_workspace_node_code
from server.app.services.node_codes import NodeCodeService

if TYPE_CHECKING:
    from server.app.db.dialect import ConnectSource
    from server.app.settings import Settings

logger = logging.getLogger(__name__)

DEMO_WORKFLOW_KEY = "education_video_problems_generation"

# (node_key, repo-relative seed source) — the git-reviewed source of truth.
DEMO_NODE_SOURCES: tuple[tuple[str, str], ...] = (
    ("intake_knowledge_points", "workflow_nodes/example_intake.py"),
    ("publish_content", "workflow_nodes/example_publish.py"),
)


def seed_demo_workspace_node_codes(
    settings: Settings,
    workspace_id: str,
    *,
    legacy_codes: dict[str, str] | None = None,
    connect_source: ConnectSource | None = None,
) -> list[str]:
    """Publish the demo code into one workspace, preserving existing versions.

    ``connect_source`` is the JobQueries facade (BOUNDARY-DATA-001, #187);
    None falls back to the settings DSN (tests, scripts).
    """
    if not settings.executor_runtime.workflows.custom_nodes_enabled:
        return []
    service = NodeCodeService(
        connect_source if connect_source is not None else settings.database_url,
        custom_nodes_enabled=True,
    )
    seeded: list[str] = []
    for node_key, relative in DEMO_NODE_SOURCES:
        source_path = settings.root_dir / relative
        code = (legacy_codes or {}).get(node_key) or source_path.read_text(encoding="utf-8")
        if seed_workspace_node_code(
            service,
            workspace_id,
            DEMO_WORKFLOW_KEY,
            node_key,
            code,
            change_note=f"workspace factory seed from {relative} (git-reviewed demo node)",
        ):
            seeded.append(node_key)
            logger.info(
                "seeded workspace demo node code: %s:%s:%s",
                workspace_id,
                DEMO_WORKFLOW_KEY,
                node_key,
            )
    return seeded
