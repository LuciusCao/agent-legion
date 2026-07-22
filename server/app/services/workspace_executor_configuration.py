from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_errors import NotFoundError
from server.app.services.workspace_executor_warnings import configuration_with_warnings


class WorkspaceExecutorConfigurationService:
    def __init__(self, job_db: JobQueries) -> None:
        self.job_db = job_db

    def get(self, workspace_id: str) -> dict[str, Any]:
        if self.job_db.get_workspace(workspace_id) is None:
            raise NotFoundError("Workspace not found")
        configuration = self.job_db.get_workspace_executor_configuration(workspace_id)
        result = configuration_with_warnings(self.job_db, workspace_id, configuration)
        result["agent_capacity"] = self.job_db.get_workspace_agent_capacity(workspace_id)
        return result
