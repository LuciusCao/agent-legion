from fastapi import APIRouter
from pydantic import BaseModel

from ..worker_control import WorkerControl


class WorkerStatusResponse(BaseModel):
    paused: bool


def create_worker_router(worker_control: WorkerControl) -> APIRouter:
    router = APIRouter(prefix="/worker", tags=["worker"])

    @router.get("/status", response_model=WorkerStatusResponse)
    def worker_status() -> WorkerStatusResponse:
        return WorkerStatusResponse(paused=worker_control.is_paused())

    @router.post("/pause", response_model=WorkerStatusResponse)
    def pause_worker() -> WorkerStatusResponse:
        worker_control.pause()
        return WorkerStatusResponse(paused=True)

    @router.post("/resume", response_model=WorkerStatusResponse)
    def resume_worker() -> WorkerStatusResponse:
        worker_control.resume()
        return WorkerStatusResponse(paused=False)

    return router
