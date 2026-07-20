from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette import concurrency

from server.app.routes.remote_auth import WorkerAuthenticator, create_worker_authorizer
from server.app.services.artifact_store import ArtifactNotFoundError, ArtifactStore
from server.app.settings import Settings


class ArtifactUploadResponse(BaseModel):
    hash: str


def create_artifacts_router(
    store: ArtifactStore, settings: Settings, broker: WorkerAuthenticator | None = None
) -> APIRouter:
    """Worker-facing artifact upload/download; auth + forwarding only (no storage logic)."""
    router = APIRouter(prefix="/artifacts", tags=["artifacts"])
    remote_config = settings.executor_runtime.remote
    authorize = create_worker_authorizer(remote_config, broker)

    @router.post("", status_code=201, response_model=ArtifactUploadResponse)
    async def upload_artifact(request: Request) -> ArtifactUploadResponse:
        authorize(request, require_worker_id=False)
        body = await request.body()
        if len(body) > remote_config.max_archive_bytes:
            raise HTTPException(status_code=413, detail="artifact too large")
        digest = await concurrency.run_in_threadpool(store.put, body)
        return ArtifactUploadResponse(hash=digest)

    @router.get("/{hash}")
    def download_artifact(hash: str, request: Request) -> FileResponse:
        authorize(request, require_worker_id=False)
        try:
            path = store.open(hash)
        except ArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="artifact not found") from exc
        return FileResponse(path, media_type="application/octet-stream", filename=hash)

    return router
