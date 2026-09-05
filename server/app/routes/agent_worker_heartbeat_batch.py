"""Batch heartbeat route for the Agent Worker data plane (protocol v5, #352).

Split from ``agent_worker_heartbeat`` for the file-size budget: one request
renews every listed lease of the authenticated Worker in a single write
transaction (``server/app/agent_broker/heartbeat_batch.py``). The wire
contracts live here too — they serve only this route (the general Worker
control-plane contracts stay in ``agent_workers_contracts``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from server.app.agent_broker import AgentExecutionBroker
from server.app.agent_broker.heartbeat_batch import MAX_BATCH_HEARTBEATS


class BatchHeartbeatItem(BaseModel):
    """One execution of a per-Worker batch heartbeat."""

    execution_id: str = Field(min_length=1, max_length=64)
    lease_id: str = Field(min_length=1, max_length=64)


class BatchHeartbeatRequest(BaseModel):
    """Body of ``POST /api/agent-executions/heartbeats``: every lease the
    sending Worker wants renewed in one transaction. Empty is legal (a Worker
    between two claims), answered with empty result lists."""

    executions: list[BatchHeartbeatItem] = Field(
        default_factory=list, max_length=MAX_BATCH_HEARTBEATS
    )


class BatchHeartbeatResponse(BaseModel):
    """Per-execution verdict of a batch heartbeat.

    ``renewed``/``lost`` partition the request items; ``lost`` carries the
    409 family (unknown id, swept lease, foreign worker) so the Worker can
    prune those leases locally instead of retrying them forever. The cancel
    body mirrors the single heartbeat's protocol-v2 shape: batch beats carry
    the same explicit cancellation list for this Worker's claimed code
    executions."""

    renewed: list[str]
    lost: list[str]
    cancelled_execution_ids: list[str]


def register_batch_heartbeat_route(
    router: APIRouter,
    broker: AgentExecutionBroker,
    authorize_worker: Callable[..., dict[str, Any]],
) -> None:
    @router.post(
        "/agent-executions/heartbeats",
        response_model=BatchHeartbeatResponse,
    )
    def heartbeat_batch(payload: BatchHeartbeatRequest, request: Request) -> BatchHeartbeatResponse:
        """Per-Worker batch heartbeat: one write transaction renews every
        listed lease of the authenticated Worker.

        Unknown/expired items come back in ``lost`` (not 5xx) so a stale item
        never blocks the renewal of its batch siblings; the cancel body
        mirrors the single heartbeat's protocol-v2 shape."""
        worker = authorize_worker(request)
        worker_id = str(worker["worker_id"])
        outcome = broker.heartbeat_batch(
            worker_id, [item.model_dump() for item in payload.executions]
        )
        cancelled = broker.cancelled_code_executions(worker_id)
        return BatchHeartbeatResponse(
            renewed=outcome["renewed"],
            lost=outcome["lost"],
            cancelled_execution_ids=cancelled,
        )
