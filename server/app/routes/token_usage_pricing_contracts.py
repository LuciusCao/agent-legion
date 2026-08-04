from __future__ import annotations

from pydantic import BaseModel, Field


class TokenUsagePricingRate(BaseModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    input_per_1m: float = Field(ge=0)
    output_per_1m: float = Field(ge=0)
    cache_read_per_1m: float = Field(ge=0)


class TokenUsagePricingConfigResponse(BaseModel):
    currency: str
    pricing: list[TokenUsagePricingRate]


class TokenUsagePricingConfigUpdate(BaseModel):
    currency: str = Field(min_length=1)
    pricing: list[TokenUsagePricingRate]
