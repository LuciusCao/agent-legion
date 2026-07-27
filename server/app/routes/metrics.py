from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from server.app.routes.metrics_contracts import MetricBucket, OpsMetricsResponse
from server.app.services.ops_metrics import OpsMetricsService


def create_metrics_router(ops_metrics: OpsMetricsService) -> APIRouter:
    router = APIRouter()

    @router.get("/metrics/overview", response_model=OpsMetricsResponse)
    def get_metrics_overview(
        granularity: Literal["minute", "hour", "day"] = "minute",
        hours: Annotated[int, Query(ge=1, le=24)] = 6,
        days: Annotated[int, Query(ge=1, le=30)] = 7,
        worker_id: str | None = None,
    ) -> OpsMetricsResponse:
        buckets = [
            MetricBucket(**bucket)
            for bucket in ops_metrics.query_series(granularity, hours, days, worker_id)
        ]
        return OpsMetricsResponse(granularity=granularity, buckets=buckets)

    return router
