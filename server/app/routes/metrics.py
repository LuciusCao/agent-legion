from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request

from server.app.auth.workspace_access import require_workspace_access
from server.app.routes.metrics_access import enforce_workspace_membership
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
        request: Request,
        user: Annotated[dict[str, Any], Depends(require_workspace_access)],
        granularity: Literal["6h", "24h", "30d"] = "6h",
        worker_id: str | None = None,
        workspace_id: str | None = None,
    ) -> OpsMetricsResponse:
        enforce_workspace_membership(request, workspace_id, user)
        buckets = [
            MetricBucket(**bucket)
            for bucket in ops_metrics.query_series(granularity, worker_id, workspace_id)
        ]
        summary = OpsMetricsSummary(**ops_metrics.query_summary(worker_id, workspace_id))
        return OpsMetricsResponse(granularity=granularity, buckets=buckets, summary=summary)

    return router
