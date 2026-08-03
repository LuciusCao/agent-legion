from fastapi import APIRouter, Request
from pydantic import BaseModel


class HealthResponse(BaseModel):
    ok: bool
    workers: dict[str, str] | None = None


def create_common_router() -> APIRouter:
    router = APIRouter(tags=["common"])

    @router.get("/health", response_model=HealthResponse)
    def health(request: Request) -> HealthResponse:
        workers = getattr(request.app.state, "worker_startup", None)
        return HealthResponse(ok=True, workers=workers or None)

    return router
