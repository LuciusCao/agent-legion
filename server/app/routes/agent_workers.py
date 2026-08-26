from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from starlette import concurrency

from server.app.agent_broker import AgentExecutionBroker
from server.app.agent_broker.agent_result_commit import commit_agent_result
from server.app.agent_broker.result_spool import discard_staged_result, spool_result_body
from server.app.agent_completion import AgentCompletionHandler
from server.app.agent_workers import AgentWorkerRegistry
from server.app.auth.dependencies import require_admin, require_user
from server.app.routes.agent_worker_claims import create_agent_worker_claim_router
from server.app.routes.agent_worker_metrics import create_agent_worker_metrics_router
from server.app.routes.agent_worker_results import parse_result_metadata
from server.app.routes.agent_workers_contracts import (
    AgentRegisterTokenCreatedResponse,
    AgentRegisterTokenRevokeResponse,
    AgentRegisterTokensResponse,
    AgentWorkerRevokeResponse,
    AgentWorkersResponse,
    AgentWorkerSummary,
    AgentWorkerWorkspace,
    CreateAgentRegisterTokenRequest,
    RegisterAgentWorkerRequest,
    RegisterAgentWorkerResponse,
)
from server.app.services.ops_metrics import OpsMetricsService
from server.app.settings import Settings

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_LEASE_HEADER = "x-agent-lease-id"


def create_agent_workers_router(
    broker: AgentExecutionBroker,
    registry: AgentWorkerRegistry,
    completion: AgentCompletionHandler,
    settings: Settings,
    ops_metrics: OpsMetricsService | None = None,
    job_artifact_objects: Any = None,
) -> APIRouter:
    router = APIRouter(tags=["agent-workers"])
    config = settings.executor_runtime.agent_workers

    def resolve_registration_scope(request: Request) -> list[dict[str, Any]]:
        """Resolve the presented registration credentials to a workspace scope.

        The Worker presents every scoped register token it holds (comma-joined
        in X-Agent-Worker-Register-Tokens); all of them must be live
        workspace-scoped tokens — any revoked or unknown token fails the whole
        registration so a stale token can never silently narrow the scope.
        Returns [{'workspace_id', 'workspace_name', 'token_ids'}] rows so the
        Worker console can label each token with its workspace name."""
        supplied = [
            token.strip()
            for token in request.headers.get("x-agent-worker-register-tokens", "").split(",")
            if token.strip()
        ]
        # 单 token 兼容头：旧版 worker 客户端仍以单值头注册（等价于一个元素）。
        supplied = supplied or [request.headers.get("x-agent-worker-register-token", "")]
        supplied = [token for token in supplied if token]
        if not supplied:
            raise HTTPException(status_code=401, detail="missing Agent Worker registration token")
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

    if ops_metrics is not None:
        router.include_router(create_agent_worker_metrics_router(ops_metrics, authorize_worker))
    router.include_router(
        create_agent_worker_claim_router(
            broker, settings, authorize_worker, require_lease_id, job_artifact_objects
        )
    )

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
            token = registry.issue_token(
                **payload.model_dump(),
                allowed_workspaces=[row["workspace_id"] for row in scope],
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RegisterAgentWorkerResponse(
            worker_token=token,
            allowed_workspaces=[row["workspace_id"] for row in scope],
            workspaces=[
                AgentWorkerWorkspace(
                    workspace_id=str(row["workspace_id"]),
                    workspace_name=str(row["workspace_name"]),
                    token_ids=[str(token_id) for token_id in row["token_ids"]],
                )
                for row in scope
            ],
        )

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
        workspace_id: str | None = None,
    ) -> AgentWorkersResponse:
        """List registered workers; workspace_id narrows to that workspace.

        The workspace view only shows workers registered with that
        workspace's scoped tokens (legacy [] scope is excluded); without the
        parameter every logged-in user still sees the full list — the UI is
        responsible for passing the current workspace, and the admin settings
        page intentionally keeps the unfiltered view."""
        return AgentWorkersResponse.model_validate({"workers": registry.list_workers(workspace_id)})

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

    @router.post("/agent-executions/{execution_id}/release-slot", status_code=204)
    def release_slot(execution_id: str, request: Request) -> Response:
        worker = authorize_worker(request)
        lease_id = require_lease_id(request)
        if not broker.release_slot(execution_id, str(worker["worker_id"]), lease_id):
            raise HTTPException(status_code=409, detail="execution is not owned by this Worker")
        return Response(status_code=204)

    @router.post("/agent-executions/{execution_id}/result", status_code=204)
    async def result(execution_id: str, request: Request) -> Response:
        worker = authorize_worker(request)
        worker_id = str(worker["worker_id"])
        lease_id = require_lease_id(request)
        # Validate metadata fully BEFORE writing the archive: malformed input
        # must produce a 400, never a 500 with an orphan file on disk.
        try:
            outcome, record = parse_result_metadata(request.headers.get("x-agent-result", "{}"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid Agent result metadata") from exc
        # Size gate: reject on the declared length before spooling the body.
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > config.max_archive_bytes:
            raise HTTPException(status_code=413, detail="Agent result archive too large")
        if broker.bundle_dir is None:
            raise HTTPException(status_code=500, detail="Agent bundle storage is unavailable")
        # Cheap ownership pre-check BEFORE spooling the body to disk: a stale
        # lease would otherwise write up to max_archive_bytes for nothing.
        # commit_agent_result re-checks under the commit to stay TOCTOU-safe.
        payload = await concurrency.run_in_threadpool(
            broker.claimed_payload, execution_id, worker_id
        )
        if payload is None or str(payload["lease_id"]) != lease_id:
            raise HTTPException(status_code=409, detail="execution is not owned by this Worker")
        staged = await spool_result_body(request, broker.bundle_dir, config.max_archive_bytes)
        try:
            # The blocking DB/disk commit runs in the threadpool: at agent scale
            # (multiple reports per second) holding the event loop here stalls
            # every heartbeat, claim, and dashboard stream behind it.
            await concurrency.run_in_threadpool(
                commit_agent_result,
                broker,
                completion,
                execution_id,
                worker_id,
                lease_id,
                outcome,
                record,
                staged,
            )
        finally:
            # A successful commit atomically renamed the staging file into
            # place; on any failure it is reclaimed here.
            discard_staged_result(staged)
        return Response(status_code=204)

    return router
