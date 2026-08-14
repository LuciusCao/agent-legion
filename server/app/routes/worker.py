from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ..auth.dependencies import reject_studio_agent_scope
from ..worker_control import WorkspaceWorkerControl


class WorkerStatusResponse(BaseModel):
    paused: bool


def create_worker_router(
    workspace_worker_control: WorkspaceWorkerControl | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/worker", tags=["worker"])

    @router.get("/status", response_model=WorkerStatusResponse)
    def worker_status(
        workspace_id: str = Query(...),
    ) -> WorkerStatusResponse:
        paused = (
            workspace_worker_control.is_paused(workspace_id)
            if workspace_worker_control is not None
            else True
        )
        return WorkerStatusResponse(paused=paused)

    @router.post(
        "/pause",
        response_model=WorkerStatusResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def pause_worker(
        workspace_id: str = Query(...),
    ) -> WorkerStatusResponse:
        if workspace_worker_control is not None:
            workspace_worker_control.pause(workspace_id)
        return WorkerStatusResponse(paused=True)

    @router.post(
        "/resume",
        response_model=WorkerStatusResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def resume_worker(
        workspace_id: str = Query(...),
    ) -> WorkerStatusResponse:
        if workspace_worker_control is not None:
            workspace_worker_control.resume(workspace_id)
        return WorkerStatusResponse(paused=False)

    return router
