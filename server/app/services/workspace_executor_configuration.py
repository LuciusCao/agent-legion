from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.jobs import JobQueries
from server.app.services.job_errors import NotFoundError
from server.app.services.workspace_executor_filter import filter_known_executors

if TYPE_CHECKING:
    from server.app.settings import Settings


class WorkspaceExecutorConfigurationService:
    def __init__(self, job_db: JobQueries, settings: Settings | None = None) -> None:
        self.job_db = job_db
        self._settings = settings

    def get(self, workspace_id: str) -> dict[str, Any]:
        if self.job_db.get_workspace(workspace_id) is None:
            raise NotFoundError("Workspace not found")
        configuration = self.job_db.get_workspace_executor_configuration(workspace_id)
        # Read the live settings at call time: executor publish/rollback/
        # archive hot-reloads settings.executor_definitions, so a dict
        # captured at construction would filter out freshly published IDs.
        definitions = self._settings.executor_definitions if self._settings else None
        configuration = filter_known_executors(configuration, definitions)
        result: dict[str, Any] = {**configuration, "migration_warnings": []}
        result["agent_capacity"] = self.job_db.get_workspace_agent_capacity(workspace_id)
        return result
