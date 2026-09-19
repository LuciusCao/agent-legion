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
from server.app.agent_control.claim_state import record_claim_state
from server.app.routes.agent_worker_claim_contracts import (
    BatchAgentClaimResponse,
    ClaimAgentExecutionRequest,
)
from server.app.routes.agent_worker_claim_response import build_batch_claim_response
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

    @router.post("/agent-executions/claim", response_model=BatchAgentClaimResponse)
    def claim(
        payload: ClaimAgentExecutionRequest, request: Request
    ) -> Response | BatchAgentClaimResponse:
        worker = authorize_worker(request, payload.worker_id)
        # A claim poll only happens with the switch on: settle the v83 state
        # to True (no write once it already is) even before a presence sync.
        record_claim_state(broker.database_dsn, worker, True)
        # #546 batch claim, #547 single-path retirement: every request is a
        # batch request now (the default limit=1 answers a one-element
        # ``claims`` list; the pre-#546 byte-identical single-object body is
        # gone — batch claim shipped in 0.7.4 and the mixed-fleet window has
        # closed). Per-pool limits keep their meaning; a request carrying
        # them is capped per pool exactly as before.
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
        return build_batch_claim_response(broker, settings, job_artifact_objects, worker, claims)

    register_heartbeat_route(router, broker, authorize_worker, require_lease_id)
    return router
