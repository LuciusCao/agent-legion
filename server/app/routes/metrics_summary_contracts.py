"""Summary card models for the ops-metrics overview response.

Split out of ``metrics_contracts.py`` to respect that module's size budget.
The summary is window-independent: tokens and gauges come from minute
samples, run stats are aggregated on demand from ``node_runs`` (always
global — the table has no worker attribution).
"""

from __future__ import annotations

from pydantic import BaseModel


class RecentHourTokenSummary(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    total_tokens: int


class RecentHourRunSummary(BaseModel):
    completed: int
    failed: int
    duration_p50_seconds: float | None
    duration_p95_seconds: float | None


class OpsMetricsSummary(BaseModel):
    online_workers: int | None
    active_executions: int | None
    recent_hour_tokens: RecentHourTokenSummary
    recent_hour_runs: RecentHourRunSummary
