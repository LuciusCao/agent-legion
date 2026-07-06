from __future__ import annotations

from pydantic import BaseModel


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
