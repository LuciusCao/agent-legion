from typing import Any

from server.app.executors.config import ExecutorConfig
from server.app.jobs import JobQueries
from server.app.services.job_errors import NotFoundError
from server.app.services.workspace_executor_filter import filter_known_executors


class WorkspaceExecutorConfigurationService:
    def __init__(
        self,
        job_db: JobQueries,
        executor_definitions: dict[str, ExecutorConfig] | None = None,
    ) -> None:
        self.job_db = job_db
        self.executor_definitions = executor_definitions

    def get(self, workspace_id: str) -> dict[str, Any]:
        if self.job_db.get_workspace(workspace_id) is None:
            raise NotFoundError("Workspace not found")
        configuration = self.job_db.get_workspace_executor_configuration(workspace_id)
        configuration = filter_known_executors(configuration, self.executor_definitions)
        result: dict[str, Any] = {**configuration, "migration_warnings": []}
        result["agent_capacity"] = self.job_db.get_workspace_agent_capacity(workspace_id)
        return result
