"""Claim / heartbeat routes for the Agent Worker data plane.

Split out of ``agent_workers.py`` for the file-size budget (mirrors
``agent_worker_metrics.py``): the factory receives the shared auth closures
from the main router.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from server.app.agent_broker import AgentExecutionBroker
from server.app.agent_broker.code_dispatch import resolve_code_manifest_config
from server.app.agent_workers import CODE_PROTOCOL_VERSION
from server.app.routes.agent_workers_contracts import (
    AgentClaimResponse,
    AgentHeartbeatResponse,
    ClaimAgentExecutionRequest,
)
from server.app.settings import Settings

logger = logging.getLogger(__name__)


def create_agent_worker_claim_router(
    broker: AgentExecutionBroker,
    settings: Settings,
    authorize_worker: Callable[..., dict[str, Any]],
    require_lease_id: Callable[[Request], str],
) -> APIRouter:
    router = APIRouter(tags=["agent-workers"])

    @router.post("/agent-executions/claim", response_model=AgentClaimResponse)
    def claim(
        payload: ClaimAgentExecutionRequest, request: Request
    ) -> Response | AgentClaimResponse:
        authorize_worker(request, payload.worker_id)
        try:
            claimed = broker.claim(
                payload.worker_id, payload.max_concurrency, payload.max_code_concurrency
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if claimed is None:
            return Response(status_code=204)
        manifest = claimed.manifest
        if claimed.kind == "code":
            # Secret injection happens on the response path only: the queued
            # manifest keeps vault references, the resolved plaintext crosses
            # the HTTPS channel and is never persisted (VAULT-SECRET-001).
            try:
                manifest = resolve_code_manifest_config(
                    manifest, broker.database_dsn, settings.config
                )
            except Exception as exc:
                # The claim already committed; a 500 lets the Worker drop the
                # attempt and the sweeper requeues after the lease expires.
                logger.exception(
                    "code manifest secret resolution failed for %s", claimed.execution_id
                )
                raise HTTPException(
                    status_code=500, detail="code manifest secret resolution failed"
                ) from exc
        return AgentClaimResponse(
            execution_id=claimed.execution_id,
            lease_id=claimed.lease_id,
            workspace_id=claimed.workspace_id,
            job_id=claimed.job_id,
            workflow_key=claimed.workflow_key,
            node_key=claimed.node_key,
            agent_id=claimed.agent_id,
            kind=claimed.kind,
            manifest=manifest,
            bundle_url=f"/api/agent-executions/{claimed.execution_id}/bundle",
        )

    @router.post(
        "/agent-executions/{execution_id}/heartbeat",
        response_model=AgentHeartbeatResponse,
    )
    def heartbeat(execution_id: str, request: Request) -> Response | AgentHeartbeatResponse:
        worker = authorize_worker(request)
        lease_id = require_lease_id(request)
        if not broker.heartbeat(execution_id, str(worker["worker_id"]), lease_id):
            raise HTTPException(status_code=409, detail="execution is not owned by this Worker")
        # Protocol v2 (batch 2): the body carries explicit cancellations for
        # this Worker's claimed kind='code' executions. v1 Workers keep the
        # legacy empty 204 (they cannot hold code executions anyway).
        if int(worker["protocol_version"]) >= CODE_PROTOCOL_VERSION:
            cancelled = broker.cancelled_code_executions(str(worker["worker_id"]))
            return AgentHeartbeatResponse(cancelled_execution_ids=cancelled)
        return Response(status_code=204)

    return router
