from __future__ import annotations

import os
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from server.app.routes.job_http import require_workflows_enabled
from server.app.settings import Settings


class StressEventRecord(BaseModel):
    job_id: str
    kind: str = "updated"


class StressEventBatchRequest(BaseModel):
    events: list[StressEventRecord]


class StressEventBatchResponse(BaseModel):
    recorded: int
    recorded_at: float


def create_job_stress_events_router(
    settings: Settings,
    job_event_buffer: Any | None,
) -> APIRouter | None:
    if os.environ.get("AGENT_LEGION_ENABLE_STRESS_EVENTS") != "1":
        return None

    router = APIRouter()

    @router.post(
        "/workspaces/{workspace_id}/events/stress",
        response_model=StressEventBatchResponse,
    )
    def record_stress_events(
        workspace_id: str,
        payload: StressEventBatchRequest,
    ) -> StressEventBatchResponse:
        require_workflows_enabled(settings)
        if job_event_buffer is None:
            raise HTTPException(status_code=503, detail="Event buffer not available")
        recorded_at = time.monotonic()
        for event in payload.events:
            job_event_buffer.record(workspace_id, event.job_id, event.kind)
        return StressEventBatchResponse(
            recorded=len(payload.events),
            recorded_at=recorded_at,
        )

    return router
