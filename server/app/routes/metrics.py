from __future__ import annotations

from typing import Literal

from fastapi import APIRouter

from server.app.routes.metrics_contracts import (
    MetricBucket,
    OpsMetricsResponse,
    OpsMetricsSummary,
)
from server.app.services.ops_metrics import OpsMetricsService


def create_metrics_router(ops_metrics: OpsMetricsService) -> APIRouter:
    router = APIRouter()

    @router.get("/metrics/overview", response_model=OpsMetricsResponse)
    def get_metrics_overview(
        granularity: Literal["6h", "24h", "30d"] = "6h",
        worker_id: str | None = None,
    ) -> OpsMetricsResponse:
        buckets = [
            MetricBucket(**bucket) for bucket in ops_metrics.query_series(granularity, worker_id)
        ]
        summary = OpsMetricsSummary(**ops_metrics.query_summary(worker_id))
        return OpsMetricsResponse(granularity=granularity, buckets=buckets, summary=summary)

    return router
