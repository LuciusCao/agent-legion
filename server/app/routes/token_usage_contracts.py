from __future__ import annotations

from pydantic import BaseModel


class TokenUsageJobResponse(BaseModel):
    job_id: str
    runs: list[dict[str, object]]
    total: dict[str, object]
    runs_with_usage: int
    runs_without_usage: int
    currency: str


class TokenUsageWorkspaceGroup(BaseModel):
    group_key: str
    node_key: str
    provider: str
    model: str
    skill_version: str
    runs: int
    avg_input_tokens: float
    avg_output_tokens: float
    avg_cache_read_tokens: float
    avg_total_tokens: float
    total_input_tokens: int
    total_output_tokens: int
    total_cache_read_tokens: int
    total_tokens: int
    total_cost: float
    avg_cost: float
    pricing_missing: bool
    coverage: float


class TokenUsageWorkspaceResponse(BaseModel):
    workspace_id: str
    currency: str
    summary: dict[str, object]
    groups: list[TokenUsageWorkspaceGroup]
    runs_with_usage: int
    runs_without_usage: int
