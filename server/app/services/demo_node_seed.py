"""Factory seed for the demo workflow's two code nodes (#96).

The open-source demo workflow (``education_video_problems_generation``) has
two code nodes whose sources are the git-reviewed files under
``workflow_nodes/``. Since the capability ``path`` binding is retired
(EXEC-CODE-001 legacy), the sources are published at startup as **global**
node_code versions (``workspace_id`` NULL, seed-if-absent) so the demo runs
on any workspace that binds the demo workflow without per-workspace setup.
Dispatch falls back to the global version when the workspace has no
published one (``resolve_dispatch_node_code``).

Admin/operator edits in Studio are workspace-scoped and never touch the
global rows; a changed source file is not re-published to existing
deployments (seed-if-absent, same contract as the built-in executor/agent
catalog seeds).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from server.app.services.node_codes import NodeCodeService

if TYPE_CHECKING:
    from server.app.settings import Settings

logger = logging.getLogger(__name__)

DEMO_WORKFLOW_KEY = "education_video_problems_generation"

# (node_key, repo-relative seed source) — the git-reviewed source of truth.
_DEMO_NODE_SOURCES: tuple[tuple[str, str], ...] = (
    ("intake_knowledge_points", "workflow_nodes/example_intake.py"),
    ("publish_content", "workflow_nodes/example_publish.py"),
)


def seed_demo_node_codes(settings: Settings) -> list[str]:
    """Publish the demo node sources as global node_code versions when absent.

    Returns the node keys seeded this run. Respects the custom-nodes gate: a
    deployment with the feature off gets no writes.
    """
    if not settings.executor_runtime.workflows.custom_nodes_enabled:
        return []
    service = NodeCodeService(settings.database_url, custom_nodes_enabled=True)
    seeded: list[str] = []
    for node_key, relative in _DEMO_NODE_SOURCES:
        source_path = settings.root_dir / relative
        code = source_path.read_text(encoding="utf-8")
        if service.seed_global(
            DEMO_WORKFLOW_KEY,
            node_key,
            code,
            change_note=f"factory seed from {relative} (git-reviewed demo node)",
        ):
            seeded.append(node_key)
            logger.info("seeded global demo node code: %s:%s", DEMO_WORKFLOW_KEY, node_key)
    return seeded
