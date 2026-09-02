"""Runtime-profile route (#359): pipeline gauges + bottleneck verdict.

Serves the recent ``ops_runtime_profile_samples`` buckets and the L2
classifier verdict over the newest bucket. The classifier's context inputs
(latest queue depth / active executions / online workers) come from the
same ops-metrics series the monitoring panel reads, so the verdict stays
consistent with what the panel shows.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request

from server.app.auth.workspace_access import require_workspace_access
from server.app.routes.runtime_profile_contracts import (
    ProfileBucket,
    ProfileVerdict,
    RuntimeProfileResponse,
)
from server.app.services.ops_metrics import OpsMetricsService
from server.app.services.runtime_profile import classify_bottleneck, query_profile_series


def create_runtime_profile_router(ops_metrics: OpsMetricsService) -> APIRouter:
    router = APIRouter()

    @router.get("/metrics/runtime-profile", response_model=RuntimeProfileResponse)
    def get_runtime_profile(
        request: Request,
        user: Annotated[dict[str, Any], Depends(require_workspace_access)],
        window: Literal["6h", "24h"] = "6h",
    ) -> RuntimeProfileResponse:
        # The pipeline profile is fleet-global by construction (one Host
        # process); workspace scoping would silently slice a shared pipeline.
        # Membership check still applies: only workspace members may read it.
        _ = user
        # Minute-resolution rows: 6h = 360 rows. 24h would be 1440 raw
        # rows (23 columns each — a heavy response), so it aggregates to
        # 5-minute bins (288 rows), the same rollup shape ops_metrics/series
        # uses for its 24h window.
        window_buckets = {"6h": 360, "24h": 288}[window]
        window_bin_seconds = {"6h": 60, "24h": 300}[window]
        series = query_profile_series(
            ops_metrics.database_dsn, buckets=window_buckets, bin_seconds=window_bin_seconds
        )
        buckets = [ProfileBucket(**_bucket_payload(row)) for row in series]
        verdict = _verdict_for(ops_metrics, series[-1] if series else {})
        return RuntimeProfileResponse(buckets=buckets, verdict=verdict)

    return router


def _bucket_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = {key: row[key] for key in row}
    payload["bucket_start"] = str(row["bucket_start"])
    return payload


def _verdict_for(ops_metrics: OpsMetricsService, latest: dict[str, Any]) -> ProfileVerdict:
    """Classify the newest bucket with the ops-series context gauges."""
    summary = ops_metrics.query_summary()
    queue = summary.get("queue") or {}
    alert = summary.get("queue_alert") or {}
    verdict = classify_bottleneck(
        latest,
        online_workers=int(summary.get("online_workers") or 0),
        queued=int(queue.get("queued") or 0),
        queue_alert=str(alert.get("kind") or ""),
    )
    return ProfileVerdict(**verdict)
