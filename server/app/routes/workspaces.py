from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.events import JobEventManager
from server.app.events.bus import workspace_channel
from server.app.routes.dashboard_events import create_dashboard_events_router
from server.app.routes.job_contracts import (
    DeleteWorkspaceResponse,
    WorkspaceCreateRequest,
    WorkspaceResponse,
    WorkspacesResponse,
    WorkspaceStatsResponse,
    WorkspaceUpdateRequest,
)
from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.routes.workspace_contracts import WorkspaceRecord
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
        require_workflows_enabled(settings)
        try:
            workspaces = [WorkspaceRecord.model_validate(w) for w in service.list_workspaces()]
            return WorkspacesResponse(workspaces=workspaces)
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.post(
        "/workspaces",
        response_model=WorkspaceResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def create_workspace(payload: WorkspaceCreateRequest) -> WorkspaceResponse:
        require_workflows_enabled(settings)
        try:
            workspace = service.create(payload.model_dump())
            return WorkspaceResponse(workspace=WorkspaceRecord.model_validate(workspace))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.get("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
    def get_workspace(workspace_id: str) -> WorkspaceResponse:
        require_workflows_enabled(settings)
        try:
            workspace = WorkspaceRecord.model_validate(service.get(workspace_id))
            return WorkspaceResponse(workspace=workspace)
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.patch(
        "/workspaces/{workspace_id}",
        response_model=WorkspaceResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def update_workspace(workspace_id: str, payload: WorkspaceUpdateRequest) -> WorkspaceResponse:
        require_workflows_enabled(settings)
        try:
            workspace = service.update(workspace_id, payload.model_dump(exclude_unset=True))
            return WorkspaceResponse(workspace=WorkspaceRecord.model_validate(workspace))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.delete(
        "/workspaces/{workspace_id}",
        response_model=DeleteWorkspaceResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def delete_workspace(workspace_id: str) -> DeleteWorkspaceResponse:
        require_workflows_enabled(settings)
        try:
            service.delete(workspace_id)
            return DeleteWorkspaceResponse(deleted=workspace_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.get("/workspaces/{workspace_id}/stats", response_model=WorkspaceStatsResponse)
    def get_workspace_stats(workspace_id: str) -> WorkspaceStatsResponse:
        require_workflows_enabled(settings)
        try:
            return WorkspaceStatsResponse(**service.stats(workspace_id))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.get(
        "/workspaces/{workspace_id}/events",
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {}}}},
    )
    async def workspace_events(request: Request, workspace_id: str) -> StreamingResponse:
        require_workflows_enabled(settings)
        if job_event_manager is None:
            raise HTTPException(status_code=503, detail="Event manager not available")
        return await job_event_manager.connect(request, workspace_channel(workspace_id))

    router.include_router(
        create_dashboard_events_router(settings, job_event_manager=job_event_manager)
    )

    return router
