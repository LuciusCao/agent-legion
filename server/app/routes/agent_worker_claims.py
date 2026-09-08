"""Claim routes for the Agent Worker data plane (split from
``agent_workers.py`` for the file budget; heartbeat lives in
``agent_worker_heartbeat``, mirrors ``agent_worker_metrics.py``).

Response assembly (manifest injection + contract build) lives in
``agent_worker_claim_response.py``; the batch claim transaction (#546) in
``server/app/agent_broker/claim_batch.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from server.app.agent_broker import AgentExecutionBroker
from server.app.agent_broker.claim_batch import claim_batch
from server.app.routes.agent_worker_claim_contracts import (
    AgentClaimResponse,
    BatchAgentClaimResponse,
    ClaimAgentExecutionRequest,
    ClaimRouteResponse,
)
from server.app.routes.agent_worker_claim_response import (
    build_batch_claim_response,
    build_claim_response,
)
from server.app.routes.agent_worker_heartbeat import register_heartbeat_route
from server.app.settings import Settings


def create_agent_worker_claim_router(
    broker: AgentExecutionBroker,
    settings: Settings,
    authorize_worker: Callable[..., dict[str, Any]],
    require_lease_id: Callable[[Request], str],
    job_artifact_objects: Any = None,
) -> APIRouter:
    router = APIRouter(tags=["agent-workers"])

    @router.post("/agent-executions/claim", response_model=ClaimRouteResponse)
    def claim(
        payload: ClaimAgentExecutionRequest, request: Request
    ) -> Response | AgentClaimResponse | BatchAgentClaimResponse:
        worker = authorize_worker(request, payload.worker_id)
        # #546 batch claim: limit > 1 promotes up to `limit` executions in one
        # transaction and answers BatchAgentClaimResponse; the default (1, or a
        # pre-#546 Worker that sends no limit) takes the legacy single-claim
        # path with a byte-identical response.
        if payload.limit > 1:
            try:
                claims = claim_batch(
                    broker,
                    payload.worker_id,
                    payload.max_concurrency,
                    payload.max_code_concurrency,
                    limit=payload.limit,
                    agent_limit=payload.agent_limit,
                    code_limit=payload.code_limit,
                )
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            return build_batch_claim_response(
                broker, settings, job_artifact_objects, worker, claims
            )
        # #338: the claiming Worker's protocol version selects the artifact
        # object form (v4+ gets .gz specs; older Workers stay raw dual-form).
        try:
            claimed = broker.claim(
                payload.worker_id, payload.max_concurrency, payload.max_code_concurrency
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if claimed is None:
            return Response(status_code=204)
        return build_claim_response(broker, settings, job_artifact_objects, worker, claimed)

    register_heartbeat_route(router, broker, authorize_worker, require_lease_id)
    return router
