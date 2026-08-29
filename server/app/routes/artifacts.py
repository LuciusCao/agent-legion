"""Worker-facing artifact upload/download routes.

Deprecated (#160 D12): current Workers upload/download job artifacts
directly through the object-storage channel (presigned URLs on the claim
manifest). This router stays for legacy Workers (per-file POST) and for
reading legacy CAS blobs (GET); new Worker code must not call POST here.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette import concurrency

from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.services.artifact_store import ArtifactNotFoundError, ArtifactStore
from server.app.settings import Settings


class ArtifactUploadResponse(BaseModel):
    hash: str


def create_artifacts_router(
    store: ArtifactStore,
    settings: Settings,
    agent_worker_registry: AgentWorkerRegistry | None = None,
) -> APIRouter:
    """Worker-facing artifact upload/download; auth + forwarding only (no storage logic)."""
    router = APIRouter(prefix="/artifacts", tags=["artifacts"])
    agent_config = settings.executor_runtime.agent_workers

    def authorize_artifact(request: Request) -> None:
        agent_token = request.headers.get("x-agent-worker-token", "")
        if agent_token and agent_worker_registry is not None:
            if agent_worker_registry.authenticate(agent_token) is not None:
                return
            raise HTTPException(status_code=401, detail="invalid Agent Worker token")
        raise HTTPException(status_code=401, detail="missing Agent Worker token")

    @router.post("", status_code=201, response_model=ArtifactUploadResponse)
    async def upload_artifact(request: Request) -> ArtifactUploadResponse:
        # authenticate() is a synchronous DB read; keep it off the loop like
        # the store.put below (deprecated route, but same-loop discipline).
        await concurrency.run_in_threadpool(authorize_artifact, request)
        declared = request.headers.get("content-length")
        if (
            declared is not None
            and declared.isdigit()
            and int(declared) > agent_config.max_archive_bytes
        ):
            raise HTTPException(status_code=413, detail="artifact too large")
        body = await request.body()
        if len(body) > agent_config.max_archive_bytes:
            raise HTTPException(status_code=413, detail="artifact too large")
        digest = await concurrency.run_in_threadpool(store.put, body)
        return ArtifactUploadResponse(hash=digest)

    @router.get("/{hash}")
    def download_artifact(hash: str, request: Request) -> FileResponse:
        authorize_artifact(request)
        try:
            path = store.open(hash)
        except ArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="artifact not found") from exc
        return FileResponse(path, media_type="application/octet-stream", filename=hash)

    return router
