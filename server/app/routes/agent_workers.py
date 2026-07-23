from __future__ import annotations

import hmac
import json
import re
import uuid
from pathlib import PurePosixPath
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from server.app.agent_broker import AgentExecutionBroker
from server.app.agent_completion import AgentCompletionHandler, AgentOutcome
from server.app.agent_workers import AgentWorkerRegistry
from server.app.settings import Settings

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_ARTIFACT_REF = re.compile(r"^sha256:[0-9a-f]{64}$")
_LEASE_HEADER = "x-agent-lease-id"
# Bounds for worker-supplied result metadata; the metadata travels in an HTTP
# header and lands in the jobs DB, so keep every field small.
_MAX_COMMAND_PARTS = 64
_MAX_OUTPUT_ARTIFACTS = 128
_MAX_ERROR_MESSAGE_CHARS = 4000
_MAX_RUN_DIR_CHARS = 256


class RegisterAgentWorkerRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=128)
    runtimes: list[str] = Field(min_length=1)
    max_concurrency: int = Field(gt=0, le=1024)
    labels: dict[str, Any] = Field(default_factory=dict)
    protocol_version: int = Field(default=1, ge=1)
    # Informational only: no agent_workers column stores it yet.
    image_version: str = Field(default="", max_length=128)


class RegisterAgentWorkerResponse(BaseModel):
    worker_token: str
    # Server-resolved workspace admission scope; [] means all workspaces.
    allowed_workspaces: list[str]


class CreateAgentRegisterTokenRequest(BaseModel):
    workspace_id: str | None = Field(default=None, max_length=128)
    label: str = Field(default="", max_length=128)


class AgentRegisterTokenCreatedResponse(BaseModel):
    token_id: str
    # Plaintext, returned exactly once at issuance.
    register_token: str
    workspace_id: str | None
    label: str


class AgentRegisterTokenSummary(BaseModel):
    token_id: str
    workspace_id: str | None
    label: str
    created_at: str
    revoked: bool


class AgentRegisterTokensResponse(BaseModel):
    tokens: list[AgentRegisterTokenSummary]


class AgentRegisterTokenRevokeResponse(BaseModel):
    revoked: bool


class ClaimAgentExecutionRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=64)


class AgentWorkerSummary(BaseModel):
    worker_id: str
    name: str
    runtimes: list[str]
    max_concurrency: int
    labels: dict[str, str]
    protocol_version: int
    # Server-side workspace admission scope; [] means all workspaces.
    allowed_workspaces: list[str]
    registered_at: str
    last_seen_at: str
    # True while the Worker's last authenticated call is within the online
    # threshold; registered-but-silent Workers show as offline.
    online: bool
    revoked: bool


class AgentWorkersResponse(BaseModel):
    workers: list[AgentWorkerSummary]


class AgentWorkerRevokeResponse(BaseModel):
    worker_id: str
    revoked: bool


class AgentClaimResponse(BaseModel):
    execution_id: str
    lease_id: str
    workspace_id: str
    job_id: str
    workflow_key: str
    node_key: str
    agent_id: str
    manifest: dict[str, Any]
    bundle_url: str


def _parse_result_metadata(raw: str) -> tuple[AgentOutcome, dict[str, Any]]:
    """Validate worker result metadata into an outcome + stored record.

    Raises ValueError on anything malformed so the route can answer 400
    before touching the archive on disk (no 500s, no orphan files)."""
    try:
        metadata = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("metadata is not valid JSON") from exc
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a JSON object")
    status = str(metadata.get("status", ""))
    if status not in {"completed", "failed", "cancelled"}:
        raise ValueError("invalid status")
    try:
        exit_code = int(metadata.get("exit_code", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid exit_code") from exc
    command_raw = metadata.get("command", [])
    if not isinstance(command_raw, (list, tuple)) or len(command_raw) > _MAX_COMMAND_PARTS:
        raise ValueError("invalid command")
    artifacts_raw = metadata.get("output_artifacts", {})
    if not isinstance(artifacts_raw, dict) or len(artifacts_raw) > _MAX_OUTPUT_ARTIFACTS:
        raise ValueError("invalid output artifacts")
    output_artifacts = {str(name): str(ref) for name, ref in artifacts_raw.items()}
    if any(not _ARTIFACT_REF.fullmatch(ref) for ref in output_artifacts.values()):
        raise ValueError("invalid output artifact reference")
    error_message = str(metadata.get("error_message", ""))[:_MAX_ERROR_MESSAGE_CHARS]
    run_dir_raw = metadata.get("run_dir", "")
    if not isinstance(run_dir_raw, str) or len(run_dir_raw) > _MAX_RUN_DIR_CHARS:
        raise ValueError("invalid run_dir")
    run_dir_relative = PurePosixPath(run_dir_raw)
    run_dir = ""
    if run_dir_raw:
        if run_dir_relative.is_absolute() or ".." in run_dir_relative.parts:
            raise ValueError("invalid run_dir")
        run_dir = run_dir_relative.as_posix()
    outcome = AgentOutcome(
        status=status,  # type: ignore[arg-type]
        exit_code=exit_code,
        error_message=error_message,
        command=tuple(str(part) for part in command_raw),
        output_artifacts=output_artifacts,
        run_dir=run_dir,
    )
    record = {
        "status": status,
        "exit_code": exit_code,
        "error_message": error_message,
        "output_artifacts": output_artifacts,
        "run_dir": run_dir,
    }
    return outcome, record


def create_agent_workers_router(
    broker: AgentExecutionBroker,
    registry: AgentWorkerRegistry,
    completion: AgentCompletionHandler,
    settings: Settings,
) -> APIRouter:
    router = APIRouter(tags=["agent-workers"])
    config = settings.executor_runtime.agent_workers

    def authorize_management(request: Request) -> None:
        if not config.register_token:
            raise HTTPException(status_code=503, detail="Agent Worker registration is disabled")
        supplied = request.headers.get("x-agent-worker-register-token", "")
        if not hmac.compare_digest(supplied, config.register_token):
            raise HTTPException(status_code=401, detail="invalid Agent Worker registration token")

    def resolve_registration_scope(request: Request) -> list[str]:
        """Resolve the presented registration credential to a workspace scope.

        The global register token admits Workers to ALL workspaces ([]); a
        scoped register token (agent_register_tokens) admits only its
        workspace. Anything else is rejected."""
        supplied = request.headers.get("x-agent-worker-register-token", "")
        if not supplied:
            raise HTTPException(status_code=401, detail="missing Agent Worker registration token")
        if config.register_token and hmac.compare_digest(supplied, config.register_token):
            return []
        scope = registry.resolve_register_scope(supplied)
        if scope is None:
            raise HTTPException(status_code=401, detail="invalid Agent Worker registration token")
        return scope

    def authorize_worker(request: Request, worker_id: str | None = None) -> dict[str, Any]:
        token = request.headers.get("x-agent-worker-token", "")
        if not token:
            authorization = request.headers.get("authorization", "")
            scheme, _, credential = authorization.partition(" ")
            if scheme.lower() == "bearer":
                token = credential
        worker = registry.authenticate(token)
        if worker is None or (worker_id is not None and worker["worker_id"] != worker_id):
            raise HTTPException(status_code=401, detail="invalid Agent Worker token")
        if int(worker["protocol_version"]) < config.min_protocol_version:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Agent Worker protocol version is below the server minimum;"
                    " upgrade and re-register the Worker"
                ),
            )
        return worker

    def require_lease_id(request: Request) -> str:
        lease_id = request.headers.get(_LEASE_HEADER, "")
        if not lease_id:
            raise HTTPException(status_code=400, detail="missing Agent lease id")
        return lease_id

    @router.post(
        "/agent-workers/register",
        status_code=201,
        response_model=RegisterAgentWorkerResponse,
    )
    def register(
        payload: RegisterAgentWorkerRequest, request: Request
    ) -> RegisterAgentWorkerResponse:
        scope = resolve_registration_scope(request)
        if payload.protocol_version < config.min_protocol_version:
            raise HTTPException(status_code=400, detail="unsupported Agent Worker protocol")
        try:
            token = registry.issue_token(**payload.model_dump(), allowed_workspaces=scope)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RegisterAgentWorkerResponse(worker_token=token, allowed_workspaces=scope)

    @router.post(
        "/agent-register-tokens",
        status_code=201,
        response_model=AgentRegisterTokenCreatedResponse,
    )
    def create_register_token(
        payload: CreateAgentRegisterTokenRequest, request: Request
    ) -> AgentRegisterTokenCreatedResponse:
        authorize_management(request)
        try:
            token_id, plaintext = registry.issue_register_token(
                workspace_id=payload.workspace_id, label=payload.label
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return AgentRegisterTokenCreatedResponse(
            token_id=token_id,
            register_token=plaintext,
            workspace_id=payload.workspace_id,
            label=payload.label,
        )

    @router.get("/agent-register-tokens", response_model=AgentRegisterTokensResponse)
    def list_register_tokens(request: Request) -> AgentRegisterTokensResponse:
        authorize_management(request)
        return AgentRegisterTokensResponse.model_validate(
            {"tokens": registry.list_register_tokens()}
        )

    @router.post(
        "/agent-register-tokens/{token_id}/revoke",
        response_model=AgentRegisterTokenRevokeResponse,
    )
    def revoke_register_token(token_id: str, request: Request) -> AgentRegisterTokenRevokeResponse:
        authorize_management(request)
        if not registry.revoke_register_token(token_id):
            raise HTTPException(status_code=404, detail="Agent register token not found")
        return AgentRegisterTokenRevokeResponse(revoked=True)

    @router.post(
        "/agent-workers/{worker_id}/revoke",
        response_model=AgentWorkerRevokeResponse,
    )
    def revoke_worker(worker_id: str, request: Request) -> AgentWorkerRevokeResponse:
        authorize_management(request)
        if not registry.revoke(worker_id):
            raise HTTPException(status_code=404, detail="Agent Worker not found")
        return AgentWorkerRevokeResponse(worker_id=worker_id, revoked=True)

    @router.get("/agent-workers", response_model=AgentWorkersResponse)
    def list_workers() -> AgentWorkersResponse:
        return AgentWorkersResponse.model_validate({"workers": registry.list_workers()})

    @router.post("/agent-executions/claim", response_model=AgentClaimResponse)
    def claim(
        payload: ClaimAgentExecutionRequest, request: Request
    ) -> Response | AgentClaimResponse:
        authorize_worker(request, payload.worker_id)
        try:
            claimed = broker.claim(payload.worker_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if claimed is None:
            return Response(status_code=204)
        return AgentClaimResponse(
            execution_id=claimed.execution_id,
            lease_id=claimed.lease_id,
            workspace_id=claimed.workspace_id,
            job_id=claimed.job_id,
            workflow_key=claimed.workflow_key,
            node_key=claimed.node_key,
            agent_id=claimed.agent_id,
            manifest=claimed.manifest,
            bundle_url=f"/api/agent-executions/{claimed.execution_id}/bundle",
        )

    @router.get("/agent-executions/{execution_id}/bundle")
    def bundle(execution_id: str, request: Request) -> FileResponse:
        worker = authorize_worker(request)
        payload = broker.claimed_payload(execution_id, str(worker["worker_id"]))
        if payload is None:
            raise HTTPException(status_code=409, detail="execution is not owned by this Worker")
        bundle_name = str(payload["manifest"].get("bundle_name", ""))
        if not _SAFE_NAME.fullmatch(bundle_name) or broker.bundle_dir is None:
            raise HTTPException(status_code=404, detail="Agent bundle not found")
        path = broker.bundle_dir / bundle_name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Agent bundle not found")
        return FileResponse(path, media_type="application/gzip", filename=bundle_name)

    @router.post("/agent-executions/{execution_id}/heartbeat", status_code=204)
    def heartbeat(execution_id: str, request: Request) -> Response:
        worker = authorize_worker(request)
        lease_id = require_lease_id(request)
        if not broker.heartbeat(execution_id, str(worker["worker_id"]), lease_id):
            raise HTTPException(status_code=409, detail="execution is not owned by this Worker")
        return Response(status_code=204)

    @router.post("/agent-executions/{execution_id}/result", status_code=204)
    async def result(execution_id: str, request: Request) -> Response:
        worker = authorize_worker(request)
        worker_id = str(worker["worker_id"])
        lease_id = require_lease_id(request)
        payload = broker.claimed_payload(execution_id, worker_id)
        if payload is None or str(payload["lease_id"]) != lease_id:
            raise HTTPException(status_code=409, detail="execution is not owned by this Worker")
        # Validate metadata fully BEFORE writing the archive: malformed input
        # must produce a 400, never a 500 with an orphan file on disk.
        try:
            outcome, record = _parse_result_metadata(request.headers.get("x-agent-result", "{}"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid Agent result metadata") from exc
        # Size gate: reject on the declared length before buffering the body.
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > config.max_archive_bytes:
            raise HTTPException(status_code=413, detail="Agent result archive too large")
        body = await request.body()
        if len(body) > config.max_archive_bytes:
            raise HTTPException(status_code=413, detail="Agent result archive too large")
        if broker.bundle_dir is None:
            raise HTTPException(status_code=500, detail="Agent bundle storage is unavailable")
        archive_name = f"{execution_id}.{uuid.uuid4().hex}.result.tar.gz"
        archive_path = broker.bundle_dir / archive_name
        succeeded = False
        try:
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            archive_path.write_bytes(body)
            # finish() commits the lease/node terminal state first; mark_done()
            # then closes the request (bound to lease_id in SQL). A crash
            # between the two leaves a claimed request whose lease is no
            # longer active, which the sweeper closes instead of requeueing.
            finished = completion.finish(
                lease_id=lease_id,
                worker_id=worker_id,
                job_id=str(payload["job_id"]),
                node_key=str(payload["node_key"]),
                manifest=payload["manifest"],
                outcome=outcome,
                archive_name=archive_name,
            )
            if not finished:
                raise HTTPException(status_code=409, detail="execution lease is no longer active")
            if broker.mark_done(execution_id, worker_id, lease_id, record) is None:
                raise HTTPException(status_code=409, detail="execution is no longer owned")
            succeeded = True
        finally:
            # The archive name is unique to this attempt — always reclaim it.
            broker.discard_result_archive(archive_name)
            if succeeded:
                # Only a fully committed result retires the shared execution
                # bundle. On 409/500 paths the bundle must survive for
                # re-queued attempts; terminal-request bundles are reaped by
                # the sweeper (AgentExecutionBroker.reap_terminal_bundles).
                broker.retire_bundle(str(payload["manifest"].get("bundle_name", "")))
        return Response(status_code=204)

    return router
