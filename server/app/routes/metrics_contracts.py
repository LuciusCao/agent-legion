from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from server.app.routes.metrics_summary_contracts import OpsMetricsSummary


class MetricBucket(BaseModel):
    bucket_start: str
    online_workers: int
    online_workers_max: int
    active_executions: int
    active_executions_max: int
    queued: int
    queued_max: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    total_tokens: int


class OpsMetricsResponse(BaseModel):
    granularity: Literal["6h", "24h", "30d"]
    buckets: list[MetricBucket]
    summary: OpsMetricsSummary
