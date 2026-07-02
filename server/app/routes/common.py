from fastapi import APIRouter
from pydantic import BaseModel

from ..db import Database
from ..settings import Settings


class HealthResponse(BaseModel):
    ok: bool


def create_common_router(
    db: Database,
    settings: Settings,
) -> APIRouter:
    _ = (db, settings)
    router = APIRouter(tags=["common"])

    @router.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(ok=True)

    return router
