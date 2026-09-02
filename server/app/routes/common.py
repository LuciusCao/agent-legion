from fastapi import APIRouter, Request
from pydantic import BaseModel

from server.app.services.health_status import pure_remote_workers_status
from server.app.storage.probe import cached_storage_status


class StorageStatus(BaseModel):
    configured: bool
    reachable: bool


class HealthResponse(BaseModel):
    ok: bool
    workers: dict[str, str] | None = None
    storage: StorageStatus | None = None


def create_common_router() -> APIRouter:
    router = APIRouter(tags=["common"])

    @router.get("/health", response_model=HealthResponse)
    def health(request: Request) -> HealthResponse:
        # #389: workers carries the pure-remote live code-Worker count.
        workers = pure_remote_workers_status(request.app.state)
        return HealthResponse(
            ok=True,
            workers=workers or None,
            storage=StorageStatus(**cached_storage_status(request.app.state)),
        )

    return router
