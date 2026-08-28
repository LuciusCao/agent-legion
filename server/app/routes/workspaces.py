from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from server.app.agent_workers import AgentWorkerRegistry
from server.app.auth.dependencies import reject_studio_agent_scope, require_admin
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
from server.app.routes.workspace_runtime_models import create_workspace_runtime_models_router
from server.app.scheduler_wakeup import notify_schedulable_work, reload_worker_scan_entries
from server.app.services.job_errors import JobServiceError
from server.app.services.workspace_configuration import WorkspaceConfigurationService
from server.app.settings import Settings


def create_workspaces_router(
    service: WorkspaceConfigurationService,
    settings: Settings,
    job_event_manager: JobEventManager | None = None,
) -> APIRouter:
    router = APIRouter()
    # Effecting mutations refuse studio-agent scoped tokens (STUDIO-AGENT-001);
    # reads stay reachable.
    guarded = APIRouter(dependencies=[Depends(reject_studio_agent_scope)])

    @router.get("/workspaces", response_model=WorkspacesResponse)
    def list_workspaces() -> WorkspacesResponse:
        require_workflows_enabled(settings)
        try:
            workspaces = [WorkspaceRecord.model_validate(w) for w in service.list_workspaces()]
            return WorkspacesResponse(workspaces=workspaces)
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @guarded.post("/workspaces", response_model=WorkspaceResponse)
    def create_workspace(
        request: Request,
        payload: WorkspaceCreateRequest,
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> WorkspaceResponse:
        require_workflows_enabled(settings)
        try:
            workspace = service.create(payload.model_dump())
        except JobServiceError as exc:
            raise_job_http_error(exc)
        # A workspace with a workflow key is a worker scan target (schema
        # v50): hot-reload the scan list so it is picked up without a
        # restart, then wake the poll loop.
        if str(workspace.get("default_workflow_key") or ""):
            reload_worker_scan_entries(request)
            notify_schedulable_work()
        return WorkspaceResponse(workspace=WorkspaceRecord.model_validate(workspace))

    @router.get("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
    def get_workspace(workspace_id: str) -> WorkspaceResponse:
        require_workflows_enabled(settings)
        try:
            workspace = WorkspaceRecord.model_validate(service.get(workspace_id))
            return WorkspaceResponse(workspace=workspace)
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @guarded.patch("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
    def update_workspace(workspace_id: str, payload: WorkspaceUpdateRequest) -> WorkspaceResponse:
        require_workflows_enabled(settings)
        try:
            workspace = service.update(workspace_id, payload.model_dump(exclude_unset=True))
            return WorkspaceResponse(workspace=WorkspaceRecord.model_validate(workspace))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @guarded.delete("/workspaces/{workspace_id}", response_model=DeleteWorkspaceResponse)
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
    # Studio 节点执行 datalist 的数据源（在线 Worker 的 runtime/model 声明）；
    # registry 是 agent_workers 的既有门面（BOUNDARY-DATA-001）。
    router.include_router(
        create_workspace_runtime_models_router(AgentWorkerRegistry(settings.database_url), settings)
    )
    router.include_router(guarded)

    return router
