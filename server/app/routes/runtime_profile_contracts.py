"""Contract models for the runtime-profile response (#359 L1/L2).

One row per minute bucket mirroring ``ops_runtime_profile_samples`` plus
the L2 classifier verdict computed from the newest bucket. Depth/rate
fields are ints; latency fields derive from ``*_seconds_total`` over the
bucket's counts (null when the stage saw no traffic).
"""

from __future__ import annotations

from pydantic import BaseModel


class ProfileBucket(BaseModel):
    bucket_start: str
    intake_runs: int
    intake_items: int
    pass_count: int
    pass_seconds_total: float
    pass_scan_seconds_max: float
    pass_slow_count: int
    enqueue_submitted: int
    enqueue_pool_skipped: int
    enqueue_pending: int
    enqueue_stock_gated: int
    claim_count: int
    claim_empty_count: int
    claim_seconds_total: float
    claim_seconds_max: float
    execute_active: int
    execute_done: int
    execute_requeued: int
    result_count: int
    result_seconds_total: float
    result_seconds_max: float
    db_pool_waiting: int
    db_pool_wait_seconds_total: float


class ProfileVerdict(BaseModel):
    stage: str
    conclusion: str
    evidence: dict[str, object]


class RuntimeProfileResponse(BaseModel):
    buckets: list[ProfileBucket]
    verdict: ProfileVerdict
