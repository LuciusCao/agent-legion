from fastapi import APIRouter, Query
from pydantic import BaseModel

from ..worker_control import WorkerControl, WorkspaceWorkerControl


class WorkerStatusResponse(BaseModel):
    paused: bool


def create_worker_router(
    worker_control: WorkerControl,
    workspace_worker_control: WorkspaceWorkerControl | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/worker", tags=["worker"])

    @router.get("/status", response_model=WorkerStatusResponse)
    def worker_status(
        workspace_id: str | None = Query(None),
    ) -> WorkerStatusResponse:
        if workspace_id is not None:
            paused = (
                workspace_worker_control.is_paused(workspace_id)
                if workspace_worker_control is not None
                else True
            )
            return WorkerStatusResponse(paused=paused)
        return WorkerStatusResponse(paused=worker_control.is_paused())

    @router.post("/pause", response_model=WorkerStatusResponse)
    def pause_worker(
        workspace_id: str | None = Query(None),
    ) -> WorkerStatusResponse:
        if workspace_id is not None:
            if workspace_worker_control is not None:
                workspace_worker_control.pause(workspace_id)
            return WorkerStatusResponse(paused=True)
        worker_control.pause()
        return WorkerStatusResponse(paused=True)

    @router.post("/resume", response_model=WorkerStatusResponse)
    def resume_worker(
        workspace_id: str | None = Query(None),
    ) -> WorkerStatusResponse:
        if workspace_id is not None:
            if workspace_worker_control is not None:
                workspace_worker_control.resume(workspace_id)
            return WorkerStatusResponse(paused=False)
        worker_control.resume()
        return WorkerStatusResponse(paused=False)

    return router
