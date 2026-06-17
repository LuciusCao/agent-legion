from fastapi import APIRouter, Request

from server.app.events import JobEventManager
from server.app.routes.job_contracts import (
    DeleteWorkspaceResponse,
    WorkspaceCreateRequest,
    WorkspaceResponse,
    WorkspacesResponse,
    WorkspaceStatsResponse,
    WorkspaceUpdateRequest,
)
from server.app.routes.job_http import raise_job_http_error, require_pipelines_enabled
from server.app.services.job_errors import JobServiceError
from server.app.services.workspace_configuration import WorkspaceConfigurationService
from server.app.settings import Settings


def create_workspaces_router(
    service: WorkspaceConfigurationService,
    settings: Settings,
    job_event_manager: JobEventManager | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/workspaces", response_model=WorkspacesResponse)
    def list_workspaces() -> WorkspacesResponse:
        require_pipelines_enabled(settings)
        try:
            return WorkspacesResponse(workspaces=service.list_workspaces())
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.post("/workspaces", response_model=WorkspaceResponse)
    def create_workspace(payload: WorkspaceCreateRequest) -> WorkspaceResponse:
        require_pipelines_enabled(settings)
        try:
            workspace = service.create(payload.model_dump())
            return WorkspaceResponse(workspace=workspace)
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.get("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
    def get_workspace(workspace_id: str) -> WorkspaceResponse:
        require_pipelines_enabled(settings)
        try:
            return WorkspaceResponse(workspace=service.get(workspace_id))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.patch("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
    def update_workspace(workspace_id: str, payload: WorkspaceUpdateRequest) -> WorkspaceResponse:
        require_pipelines_enabled(settings)
        try:
            workspace = service.update(workspace_id, payload.model_dump(exclude_unset=True))
            return WorkspaceResponse(workspace=workspace)
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.delete("/workspaces/{workspace_id}", response_model=DeleteWorkspaceResponse)
    def delete_workspace(workspace_id: str) -> DeleteWorkspaceResponse:
        require_pipelines_enabled(settings)
        try:
            service.delete(workspace_id)
            return DeleteWorkspaceResponse(deleted=workspace_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.get("/workspaces/{workspace_id}/stats", response_model=WorkspaceStatsResponse)
    def get_workspace_stats(workspace_id: str) -> WorkspaceStatsResponse:
        require_pipelines_enabled(settings)
        try:
            return WorkspaceStatsResponse(**service.stats(workspace_id))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.get("/workspaces/{workspace_id}/events")
    async def workspace_events(request: Request, workspace_id: str):
        require_pipelines_enabled(settings)
        if job_event_manager is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=503, detail="Event manager not available")
        return await job_event_manager.connect(request, workspace_id)

    return router
