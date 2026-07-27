from __future__ import annotations

import hmac
import re
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from server.app.agent_broker import AgentExecutionBroker
from server.app.agent_completion import AgentCompletionHandler
from server.app.agent_workers import AgentWorkerRegistry
from server.app.auth.dependencies import require_admin, require_user
from server.app.routes.agent_worker_results import parse_result_metadata
from server.app.settings import Settings

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_LEASE_HEADER = "x-agent-lease-id"


class RegisterAgentWorkerRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=128)
    runtimes: list[str] = Field(min_length=1)
    capabilities: list[str] = Field(default_factory=list)
    models: list[dict[str, str]] = Field(default_factory=list)
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
    # Live re-declaration of the worker's machine-wide capacity: the Host
    # records it as the enforced max_concurrency, so dynamic resizes on the
    # worker take effect without re-registration.
    max_concurrency: int | None = Field(default=None, gt=0, le=1024)


class AgentWorkerSummary(BaseModel):
    worker_id: str
    name: str
    runtimes: list[str]
    capabilities: list[str]
    models: list[dict[str, str]]
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


def create_agent_workers_router(
    broker: AgentExecutionBroker,
    registry: AgentWorkerRegistry,
    completion: AgentCompletionHandler,
    settings: Settings,
) -> APIRouter:
    router = APIRouter(tags=["agent-workers"])
    config = settings.executor_runtime.agent_workers

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
        payload: CreateAgentRegisterTokenRequest,
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> AgentRegisterTokenCreatedResponse:
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
    def list_register_tokens(
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> AgentRegisterTokensResponse:
        return AgentRegisterTokensResponse.model_validate(
            {"tokens": registry.list_register_tokens()}
        )

    @router.post(
        "/agent-register-tokens/{token_id}/revoke",
        response_model=AgentRegisterTokenRevokeResponse,
    )
    def revoke_register_token(
        token_id: str,
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> AgentRegisterTokenRevokeResponse:
        if not registry.revoke_register_token(token_id):
            raise HTTPException(status_code=404, detail="Agent register token not found")
        return AgentRegisterTokenRevokeResponse(revoked=True)

    @router.get("/agent-workers/self", response_model=AgentWorkerSummary)
    def get_worker_self(request: Request) -> AgentWorkerSummary:
        """Let a Worker inspect only its own registration with its issued token."""
        return AgentWorkerSummary.model_validate(authorize_worker(request))

    @router.post(
        "/agent-workers/{worker_id}/revoke",
        response_model=AgentWorkerRevokeResponse,
    )
    def revoke_worker(
        worker_id: str,
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> AgentWorkerRevokeResponse:
        if not registry.revoke(worker_id):
            raise HTTPException(status_code=404, detail="Agent Worker not found")
        return AgentWorkerRevokeResponse(worker_id=worker_id, revoked=True)

    @router.get("/agent-workers", response_model=AgentWorkersResponse)
    def list_workers(
        _user: Annotated[dict[str, Any], Depends(require_user)],
    ) -> AgentWorkersResponse:
        return AgentWorkersResponse.model_validate({"workers": registry.list_workers()})

    @router.post("/agent-executions/claim", response_model=AgentClaimResponse)
    def claim(
        payload: ClaimAgentExecutionRequest, request: Request
    ) -> Response | AgentClaimResponse:
        authorize_worker(request, payload.worker_id)
        try:
            claimed = broker.claim(payload.worker_id, payload.max_concurrency)
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
            outcome, record = parse_result_metadata(request.headers.get("x-agent-result", "{}"))
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
