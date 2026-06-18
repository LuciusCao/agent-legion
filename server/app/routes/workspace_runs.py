from fastapi import APIRouter

from server.app.routes.job_contracts import WorkspaceDagResponse, WorkspaceRunsResponse
from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.services.job_errors import JobServiceError
from server.app.services.job_queries import JobQueryService
from server.app.settings import Settings


def create_workspace_runs_router(service: JobQueryService, settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.get("/workspaces/{workspace_id}/runs", response_model=WorkspaceRunsResponse)
    def list_workspace_runs(
        workspace_id: str,
        status: str | None = None,
        node_key: str | None = None,
        job_id: str | None = None,
        limit: int = 100,
    ) -> WorkspaceRunsResponse:
        require_workflows_enabled(settings)
        try:
            return WorkspaceRunsResponse(
                runs=service.workspace_runs(workspace_id, status, node_key, job_id, limit)
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.get("/workspaces/{workspace_id}/dag", response_model=WorkspaceDagResponse)
    def get_workspace_dag(workspace_id: str) -> WorkspaceDagResponse:
        require_workflows_enabled(settings)
        try:
            return WorkspaceDagResponse(**service.workspace_dag(workspace_id))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
