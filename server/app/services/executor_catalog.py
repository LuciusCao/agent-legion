from typing import Any

from server.app.agents import AgentStatusManager
from server.app.jobs import JobQueries
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.settings import Settings


class ExecutorCatalogService:
    def __init__(
        self,
        job_db: JobQueries,
        settings: Settings,
        agent_manager: AgentStatusManager,
    ):
        self.job_db = job_db
        self.settings = settings
        self.agent_manager = agent_manager

    def _workspace(self, workspace_id: str) -> dict[str, Any]:
        workspace = self.job_db.get_workspace(workspace_id)
        if workspace is None:
            raise NotFoundError("Workspace not found")
        return workspace

    def list_assignments(self, workspace_id: str) -> list[dict[str, Any]]:
        self._workspace(workspace_id)
        return [
            {**assignment, "workspace_id": workspace_id}
            for assignment in self.job_db.list_workspace_agents(workspace_id)
        ]

    def assign(self, workspace_id: str, agent_id: str, limit: int) -> dict[str, Any]:
        self._workspace(workspace_id)
        if limit < 1:
            raise InvalidOperationError("concurrency_limit must be at least 1")
        return self.job_db.upsert_workspace_agent_assignment(workspace_id, agent_id, limit)
