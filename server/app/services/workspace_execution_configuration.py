from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.jobs import JobQueries
from server.app.services.job_errors import NotFoundError

if TYPE_CHECKING:
    from server.app.settings import Settings


class WorkspaceExecutionConfigurationService:
    """Workspace execution configuration read model (P-0.5: node limits only).

    Allocations and bindings no longer exist (schema v47); issue #198 renamed
    the class from the pre-retirement ``WorkspaceExecutorConfigurationService``
    wording.
    """

    def __init__(self, job_db: JobQueries, settings: Settings | None = None) -> None:
        self.job_db = job_db
        self._settings = settings

    def get(self, workspace_id: str) -> dict[str, Any]:
        if self.job_db.get_workspace(workspace_id) is None:
            raise NotFoundError("Workspace not found")
        return {
            "node_limits": self.job_db.get_workspace_node_limits(workspace_id),
            "migration_warnings": [],
            "agent_capacity": self.job_db.get_workspace_agent_capacity(workspace_id),
        }
