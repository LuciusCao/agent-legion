"""Worker presence route: the self record plus the reported claim switch.

``GET /agent-workers/self`` keeps a Worker's liveness fresh but carries no
state; a Worker with claiming disabled therefore looked identical to one that
was about to pick up work. The presence sync replaces that GET on newer
Workers: same response, plus ``claim_enabled`` is recorded (v83) so the Host
UI can distinguish「在线·领取中」from「在线·未领取」. Older Workers keep
calling the GET and read back ``claim_enabled: null``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request

from server.app.agent_control.claim_state import record_claim_state
from server.app.routes.agent_workers_contracts import AgentWorkerSummary, WorkerPresenceRequest


def register_presence_route(
    router: APIRouter,
    database_dsn: Any,
    authorize_worker: Callable[..., dict[str, Any]],
) -> None:
    @router.post("/agent-workers/self/presence", response_model=AgentWorkerSummary)
    def report_presence(payload: WorkerPresenceRequest, request: Request) -> AgentWorkerSummary:
        """Refresh liveness and record the Worker's claim switch; answers the self record."""
        worker = authorize_worker(request)
        record_claim_state(database_dsn, worker, payload.claim_enabled)
        return AgentWorkerSummary.model_validate({**worker, "claim_enabled": payload.claim_enabled})
