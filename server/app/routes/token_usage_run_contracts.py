from __future__ import annotations

from pydantic import BaseModel


class RunUsageCost(BaseModel):
    input: float
    output: float
    cache_read: float
    total: float
    currency: str
    pricing_missing: bool


class RunUsage(BaseModel):
    node_run_id: int
    node_key: str
    provider: str
    model: str
    skill_version: str
    message_count: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    total_tokens: int
    cost: RunUsageCost
    is_complete: bool
    usage_source: str


class TokenUsageRunResponse(BaseModel):
    job_id: str
    run_id: int
    usage: RunUsage | None
    reason: str | None
