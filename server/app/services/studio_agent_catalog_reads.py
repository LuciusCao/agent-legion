"""Read-only catalog visibility for the studio-agent tool surface (#633).

The authoring agent needs to READ what it cannot edit: agent definitions
(draft/published, all fields), the runtime tool catalog (a code-defined
static catalog, EXEC-RUNTIME-CATALOG-001 — never editable at runtime) and
the workspace's runtime → provider → models view aggregated from online
Workers (EXEC-RUNTIME-MODELS-001 — worker-owned, never editable from here).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.services.agent_runtime_catalog import AgentRuntimeCatalogService
from server.app.services.agent_service import AgentService
from server.app.services.job_errors import NotFoundError
from server.app.services.versioned_entities import VersionedEntity
from server.app.services.workspace_runtime_models import workspace_runtime_models

if TYPE_CHECKING:
    from server.app.jobs import JobQueries


class StudioAgentCatalogReads:
    """Agent-definition / runtime / model reads behind the tool surface."""

    def __init__(self, job_db: JobQueries) -> None:
        self._job_db = job_db
        self._runtime_catalog = AgentRuntimeCatalogService()
        self._worker_registry = AgentWorkerRegistry(job_db)

    def list_agent_definitions(self, workspace_id: str) -> list[VersionedEntity]:
        """Latest version per Agent (a pending draft beats the published row)."""
        self._require_workspace(workspace_id)
        return AgentService(self._job_db, workspace_id).list_latest()

    def runtime_models(self, workspace_id: str) -> dict[str, dict[str, list[str]]]:
        """``{runtime: {provider: [models]}}`` from the workspace's online
        Workers — the hint surface for authoring node ``execution`` overrides."""
        self._require_workspace(workspace_id)
        return workspace_runtime_models(self._worker_registry, workspace_id)

    def agent_runtimes(self) -> dict[str, Any]:
        """Per-runtime tool catalog projection (tool names, tiers, activation)."""
        return self._runtime_catalog.tool_catalog()

    def _require_workspace(self, workspace_id: str) -> None:
        if self._job_db.get_workspace(workspace_id) is None:
            raise NotFoundError("Workspace not found")
