from fastapi import APIRouter
from pydantic import BaseModel

from ..db import Database
from ..settings import Settings
from ..worker_control import WorkerControl


class HealthResponse(BaseModel):
    ok: bool


def create_common_router(
    db: Database, settings: Settings, worker_control: WorkerControl
) -> APIRouter:
    router = APIRouter(tags=["common"])

    @router.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(ok=True)

    @router.post("/worker/tick")
    def worker_tick() -> dict[str, bool]:
        worker_control.request_tick()
        return {"accepted": True}

    return router
