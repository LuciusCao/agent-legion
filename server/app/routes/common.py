from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from server.app.services.health_status import pure_remote_workers_status
from server.app.storage.probe import cached_storage_status
from server.app.studio_chat.instance_probe import PROBE_PARAM, instance_proof, valid_nonce


class StorageStatus(BaseModel):
    configured: bool
    reachable: bool


class HealthResponse(BaseModel):
    ok: bool
    workers: dict[str, str] | None = None
    storage: StorageStatus | None = None
    # #915: present only for a valid ?instance_probe=<nonce> (the Studio
    # api_base self-check, studio_chat/instance_probe.py); omitted otherwise.
    instance_proof: str | None = None


def create_common_router() -> APIRouter:
    router = APIRouter(tags=["common"])

    # exclude_unset: instance_proof is only set for a valid probe nonce, so
    # a plain /api/health body stays exactly what it was before #915.
    @router.get("/health", response_model=HealthResponse, response_model_exclude_unset=True)
    def health(
        request: Request,
        instance_probe: str | None = Query(default=None, alias=PROBE_PARAM),
    ) -> HealthResponse:
        # #389: workers carries the pure-remote live code-Worker count.
        workers = pure_remote_workers_status(request.app.state)
        response = HealthResponse(
            ok=True,
            workers=workers or None,
            storage=StorageStatus(**cached_storage_status(request.app.state)),
        )
        if valid_nonce(instance_probe):
            # Invalid nonces are ignored (no field, no 4xx).
            response.instance_proof = instance_proof(str(instance_probe))
        return response

    return router
