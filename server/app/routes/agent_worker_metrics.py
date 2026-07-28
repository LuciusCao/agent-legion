from __future__ import annotations

from typing import Annotated, Any, Literal, Protocol

from fastapi import APIRouter, Query, Request

from server.app.routes.metrics_contracts import MetricBucket, OpsMetricsResponse
from server.app.services.ops_metrics import OpsMetricsService


class AuthorizeWorker(Protocol):
    def __call__(
        self,
        request: Request,
        worker_id: str | None = None,
    ) -> dict[str, Any]: ...


def create_agent_worker_metrics_router(
    ops_metrics: OpsMetricsService,
    authorize_worker: AuthorizeWorker,
) -> APIRouter:
    """Expose only the authenticated Worker's own metrics slice."""
    router = APIRouter()

    @router.get("/agent-workers/self/metrics", response_model=OpsMetricsResponse)
    def get_worker_metrics(
        request: Request,
        granularity: Literal["minute", "hour", "day"] = "minute",
        hours: Annotated[int, Query(ge=1, le=24)] = 6,
        days: Annotated[int, Query(ge=1, le=30)] = 7,
    ) -> OpsMetricsResponse:
        worker = authorize_worker(request)
        buckets = [
            MetricBucket(**bucket)
            for bucket in ops_metrics.query_series(
                granularity,
                hours,
                days,
                str(worker["worker_id"]),
            )
        ]
        return OpsMetricsResponse(granularity=granularity, buckets=buckets)

    return router
