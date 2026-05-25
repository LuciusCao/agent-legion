from fastapi import APIRouter
from pydantic import BaseModel

from ..db import Database
from ..settings import Settings
from ..worker import process_next


class HealthResponse(BaseModel):
    ok: bool


def create_common_router(db: Database, settings: Settings) -> APIRouter:
    router = APIRouter(tags=["common"])

    @router.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return {"ok": True}

    @router.post("/worker/tick")
    def worker_tick() -> dict[str, bool]:
        return {"processed": process_next(db, settings)}

    return router
