from __future__ import annotations

from pydantic import BaseModel

from server.app.routes.token_usage_run_contracts import (
    RunUsage,
    TokenUsageCostBreakdown,
)
from server.app.routes.token_usage_workspace_group_contract import (
    TokenUsageWorkspaceGroup,
)


class TokenUsageRunItem(BaseModel):
    run_id: int
    node_key: str
    status: str
    usage: RunUsage | None
    reason: str | None


class TokenUsageTotal(BaseModel):
    message_count: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    total_tokens: int
    cost: TokenUsageCostBreakdown | None
    pricing_missing: bool


class TokenUsageSummary(TokenUsageTotal):
    pass


class TokenUsageJobResponse(BaseModel):
    job_id: str
    runs: list[TokenUsageRunItem]
    total: TokenUsageTotal
    runs_with_usage: int
    runs_without_usage: int
    currency: str


class TokenUsageWorkspaceResponse(BaseModel):
    workspace_id: str
    currency: str
    summary: TokenUsageSummary
    groups: list[TokenUsageWorkspaceGroup]
    runs_with_usage: int
    runs_without_usage: int
