from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from server.app.executors.models import ExecutionStatus
from server.app.executors.remote_broker import RemoteExecutionBroker, RemoteOutcome
from server.app.settings import Settings

logger = logging.getLogger(__name__)

_VALID_STATUSES: tuple[ExecutionStatus, ...] = ("completed", "failed", "cancelled")


class RegisterRequest(BaseModel):
    worker_id: str = Field(min_length=1)
    name: str = ""
    capabilities: list[str] = Field(min_length=1)
    slots: int = Field(gt=0)


class ClaimRequest(BaseModel):
    worker_id: str = Field(min_length=1)
    capabilities: list[str] = Field(min_length=1)


class ClaimResponse(BaseModel):
    execution_id: str
    job_id: str
    node_key: str
    capability: str
    bundle_url: str
    manifest: dict[str, Any]


def create_remote_router(broker: RemoteExecutionBroker, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/remote", tags=["remote"])
    remote_config = settings.executor_runtime.remote

    def _authorize(request: Request) -> str:
        if not remote_config.worker_token:
            raise HTTPException(status_code=503, detail="remote execution is not enabled")
        if request.headers.get("x-worker-token") != remote_config.worker_token:
            raise HTTPException(status_code=401, detail="invalid worker token")
        worker_id = request.headers.get("x-worker-id", "")
        if not worker_id:
            raise HTTPException(status_code=400, detail="missing X-Worker-Id header")
        return worker_id

    @router.post("/register", status_code=204)
    def register(payload: RegisterRequest, request: Request) -> Response:
        _authorize(request)
        broker.register_worker(payload.worker_id, payload.name, payload.capabilities, payload.slots)
        return Response(status_code=204)

    @router.post("/claim", response_model=ClaimResponse)
    def claim(payload: ClaimRequest, request: Request) -> ClaimResponse | Response:
        _authorize(request)
        claimed = broker.dequeue(payload.worker_id, frozenset(payload.capabilities))
        if claimed is None:
            return Response(status_code=204)
        broker.touch_worker(payload.worker_id)
        return ClaimResponse(
            execution_id=claimed.execution_id,
            job_id=claimed.job_id,
            node_key=claimed.node_key,
            capability=claimed.capability,
            bundle_url=claimed.bundle_url,
            manifest=claimed.manifest,
        )

    @router.get(
        "/executions/{execution_id}/bundle",
        response_class=FileResponse,
        responses={200: {"content": {"application/gzip": {}}}},
    )
    def download_bundle(execution_id: str, request: Request) -> FileResponse:
        worker_id = _authorize(request)
        bundle_name = broker.bundle_name_for(execution_id, worker_id)
        if bundle_name is None:
            raise HTTPException(status_code=404, detail="unknown or unclaimed execution")
        path = broker.bundle_dir / bundle_name
        if not path.is_file():
            raise HTTPException(status_code=410, detail="bundle is no longer available")
        return FileResponse(path, media_type="application/gzip", filename=bundle_name)

    @router.post("/executions/{execution_id}/heartbeat", status_code=204)
    def heartbeat(execution_id: str, request: Request) -> Response:
        worker_id = _authorize(request)
        if not broker.heartbeat(execution_id, worker_id):
            raise HTTPException(status_code=409, detail="claim lost")
        return Response(status_code=204)

    @router.post("/executions/{execution_id}/result", status_code=204)
    async def report_result(execution_id: str, request: Request) -> Response:
        worker_id = _authorize(request)
        meta_raw = request.headers.get("x-remote-result")
        if not meta_raw:
            raise HTTPException(status_code=400, detail="missing X-Remote-Result header")
        try:
            meta = json.loads(meta_raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="invalid X-Remote-Result JSON") from exc
        status = meta.get("status")
        if status not in _VALID_STATUSES:
            raise HTTPException(status_code=400, detail=f"invalid status: {status!r}")
        body = await request.body()
        if len(body) > remote_config.max_archive_bytes:
            raise HTTPException(status_code=413, detail="result archive too large")
        archive_name = f"{execution_id}.result.tar.gz"
        archive_path = broker.bundle_dir / archive_name
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path.write_bytes(body)
        outcome = RemoteOutcome(
            status=status,
            exit_code=int(meta.get("exit_code", 1)),
            error_message=str(meta.get("error_message", "")),
            command=tuple(str(part) for part in meta.get("command", [])),
            skill_version=str(meta.get("skill_version", "")),
            result_archive_name=archive_name,
        )
        if not broker.complete(execution_id, worker_id, outcome):
            archive_path.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail="execution is not claimed by this worker")
        return Response(status_code=204)

    return router
