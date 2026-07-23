from fastapi import APIRouter
from pydantic import BaseModel


class HealthResponse(BaseModel):
    ok: bool


def create_common_router() -> APIRouter:
    router = APIRouter(tags=["common"])

    @router.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(ok=True)

    return router
