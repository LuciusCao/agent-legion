"""Heartbeat route for the Agent Worker data plane.

Split from ``agent_worker_claims`` for the file-size budget (mirrors the
original ``agent_workers.py`` → claims split): the claim factory mounts this
registration on the shared router. The per-Worker batch heartbeat (protocol
v5, #352) lives in ``agent_worker_heartbeat_batch``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from server.app.agent_broker import AgentExecutionBroker
from server.app.agent_control.registry import CODE_PROTOCOL_VERSION
from server.app.routes.agent_worker_heartbeat_batch import register_batch_heartbeat_route
from server.app.routes.agent_workers_contracts import AgentHeartbeatResponse


def register_heartbeat_route(
    router: APIRouter,
    broker: AgentExecutionBroker,
    authorize_worker: Callable[..., dict[str, Any]],
    require_lease_id: Callable[[Request], str],
) -> None:
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

    register_batch_heartbeat_route(router, broker, authorize_worker)
