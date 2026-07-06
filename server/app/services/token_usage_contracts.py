from __future__ import annotations

from pydantic import BaseModel


class CostBreakdown(BaseModel):
    currency: str
    input: float
    output: float
    cache_read: float
    total: float
    pricing_missing: bool
