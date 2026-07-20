from __future__ import annotations

import dataclasses
import hmac
import json
import re
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette import concurrency

from server.app.executors.models import ExecutionStatus
from server.app.executors.remote_broker import RemoteExecutionBroker, RemoteOutcome
from server.app.routes.remote_auth import create_worker_authorizer
from server.app.services.artifact_store import ArtifactNotFoundError, ArtifactStore
from server.app.settings import Settings

_VALID_STATUSES: tuple[ExecutionStatus, ...] = ("completed", "failed", "cancelled")
_EXECUTION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_OUTPUT_HASH_RE = re.compile(r"sha256:[0-9a-f]{64}")


class RegisterRequest(BaseModel):
    worker_id: str = Field(min_length=1)
    name: str = ""
    capabilities: list[str] = Field(min_length=1)
    slots: int = Field(gt=0)


class WorkerTokenRegisterRequest(BaseModel):
    worker_id: str = Field(min_length=1)
    name: str = ""
    capabilities: list[str] = Field(min_length=1)
    slots: int = Field(gt=0)
    labels: dict[str, Any] = Field(default_factory=dict)


class WorkerTokenRegisterResponse(BaseModel):
    # The plaintext per-worker token is returned exactly once; only its
    # sha256 hash is persisted server-side.
    worker_token: str


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
    # Present on new servers; old workers ignore it, new workers require it.
    command_spec: dict[str, Any] | None = None


class RemoteWorkerInfo(BaseModel):
    worker_id: str
    name: str
    capabilities: list[str]
    slots: int
    registered_at: str
    last_seen_at: str


class WorkersResponse(BaseModel):
    workers: list[RemoteWorkerInfo]


def create_remote_router(
    broker: RemoteExecutionBroker,
    settings: Settings,
    artifact_store: ArtifactStore | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/remote", tags=["remote"])
    remote_config = settings.executor_runtime.remote
    _authorize = create_worker_authorizer(remote_config, broker)

    def _authorize_management(request: Request) -> None:
        """Token issuance/revocation accept only the global management token."""
        if not remote_config.worker_token:
            raise HTTPException(status_code=503, detail="remote execution is not enabled")
        token = request.headers.get("x-worker-token", "")
        if not hmac.compare_digest(token, remote_config.worker_token):
            raise HTTPException(status_code=401, detail="invalid management token")

    def _validate_execution_id(execution_id: str) -> None:
        if not _EXECUTION_ID_RE.match(execution_id):
            raise HTTPException(status_code=400, detail="invalid execution id")

    @router.post("/workers/register", status_code=201, response_model=WorkerTokenRegisterResponse)
    def register_worker_token(
        payload: WorkerTokenRegisterRequest, request: Request
    ) -> WorkerTokenRegisterResponse:
        _authorize_management(request)
        try:
            token = broker.issue_worker_token(
                payload.worker_id, payload.name, payload.capabilities, payload.slots, payload.labels
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return WorkerTokenRegisterResponse(worker_token=token)

    @router.post("/workers/{worker_id}/revoke", status_code=204)
    def revoke_worker_token(worker_id: str, request: Request) -> Response:
        _authorize_management(request)
        if not broker.revoke_worker(worker_id):
            raise HTTPException(status_code=404, detail="unknown worker")
        return Response(status_code=204)

    @router.post("/register", status_code=204)
    def register(payload: RegisterRequest, request: Request) -> Response:
        _authorize(request, payload.worker_id)
        broker.register_worker(payload.worker_id, payload.name, payload.capabilities, payload.slots)
        return Response(status_code=204)

    @router.get("/workers", response_model=WorkersResponse)
    def list_workers(request: Request) -> WorkersResponse:
        _authorize(request, require_worker_id=False)
        return WorkersResponse(workers=[RemoteWorkerInfo(**w) for w in broker.list_workers()])

    @router.post("/claim", response_model=ClaimResponse)
    def claim(payload: ClaimRequest, request: Request) -> ClaimResponse | Response:
        _authorize(request, payload.worker_id)
        claimed = broker.dequeue(payload.worker_id, frozenset(payload.capabilities))
        if claimed is None:
            return Response(status_code=204)
        broker.touch_worker(payload.worker_id)
        return ClaimResponse(**dataclasses.asdict(claimed))

    @router.get(
        "/executions/{execution_id}/bundle",
        response_class=FileResponse,
        responses={200: {"content": {"application/gzip": {}}}},
    )
    def download_bundle(execution_id: str, request: Request) -> FileResponse:
        worker_id = str(_authorize(request)["worker_id"])
        _validate_execution_id(execution_id)
        bundle_name = broker.bundle_name_for(execution_id, worker_id)
        if bundle_name is None:
            raise HTTPException(status_code=404, detail="unknown or unclaimed execution")
        path = broker.bundle_dir / bundle_name
        if not path.is_file():
            raise HTTPException(status_code=410, detail="bundle is no longer available")
        return FileResponse(path, media_type="application/gzip", filename=bundle_name)

    @router.post("/executions/{execution_id}/heartbeat", status_code=204)
    def heartbeat(execution_id: str, request: Request) -> Response:
        worker_id = str(_authorize(request)["worker_id"])
        _validate_execution_id(execution_id)
        if not broker.heartbeat(execution_id, worker_id):
            raise HTTPException(status_code=409, detail="claim lost")
        return Response(status_code=204)

    @router.post("/executions/{execution_id}/result", status_code=204)
    async def report_result(execution_id: str, request: Request) -> Response:
        worker_id = str(_authorize(request)["worker_id"])
        _validate_execution_id(execution_id)
        meta_raw = request.headers.get("x-remote-result")
        if not meta_raw:
            raise HTTPException(status_code=400, detail="missing X-Remote-Result header")
        try:
            meta = json.loads(meta_raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="invalid X-Remote-Result JSON") from exc
        if not isinstance(meta, dict):
            raise HTTPException(status_code=400, detail="X-Remote-Result must be a JSON object")
        status = meta.get("status")
        if status not in _VALID_STATUSES:
            raise HTTPException(status_code=400, detail=f"invalid status: {status!r}")
        try:
            exit_code = int(meta.get("exit_code", 1))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="invalid exit_code") from exc
        # Workers upload outputs as artifacts first, then reference them here.
        output_refs = meta.get("output_artifacts") or {}
        if not isinstance(output_refs, dict):
            raise HTTPException(status_code=400, detail="output_artifacts must be an object")
        for name, ref in output_refs.items():
            if not _OUTPUT_HASH_RE.fullmatch(str(ref)):
                raise HTTPException(status_code=400, detail=f"invalid artifact hash: {name!r}")
            if artifact_store is None:
                continue
            try:
                artifact_store.open(str(ref).split(":", 1)[1])
            except ArtifactNotFoundError as exc:
                raise HTTPException(
                    status_code=409, detail=f"upload output artifact first: {name!r}"
                ) from exc
        body = await request.body()
        if len(body) > remote_config.max_archive_bytes:
            raise HTTPException(status_code=413, detail="result archive too large")
        # Unique staging name: concurrent duplicate reports cannot clobber each other.
        staging_path = broker.bundle_dir / f"{execution_id}.{uuid4().hex}.result.tar.gz.uploading"
        broker.bundle_dir.mkdir(parents=True, exist_ok=True)
        staging_path.write_bytes(body)
        outcome = RemoteOutcome(
            status=status,
            exit_code=exit_code,
            error_message=str(meta.get("error_message", "")),
            command=tuple(str(part) for part in meta.get("command", [])),
            skill_version=str(meta.get("skill_version", "")),
            result_archive_name=f"{execution_id}.result.tar.gz",
            output_artifacts={str(k): str(v) for k, v in output_refs.items()},
        )
        # Broker renames and publishes in one critical section; waiters never race the bytes.
        complete_args = (execution_id, worker_id, outcome, staging_path)
        if not await concurrency.run_in_threadpool(broker.complete_with_archive, *complete_args):
            staging_path.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail="execution is not claimed by this worker")
        return Response(status_code=204)

    return router
