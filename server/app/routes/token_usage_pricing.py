from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import require_admin
from server.app.routes.token_usage_pricing_contracts import (
    TokenUsagePricingConfigResponse,
    TokenUsagePricingConfigUpdate,
)
from server.app.services.token_usage_pricing_store import TokenUsagePricingStore
from server.app.settings import Settings


def create_token_usage_pricing_router(job_queries, settings: Settings) -> APIRouter:
    """Admin endpoints managing the global token_usage pricing document."""
    router = APIRouter()
    store = TokenUsagePricingStore(job_queries)

    @router.get(
        "/admin/token-usage-pricing",
        response_model=TokenUsagePricingConfigResponse,
    )
    def get_token_usage_pricing(
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> TokenUsagePricingConfigResponse:
        document = store.get() or {}
        return TokenUsagePricingConfigResponse(
            currency=str(document.get("currency", "")),
            pricing=document.get("pricing", []),
        )

    @router.put(
        "/admin/token-usage-pricing",
        response_model=TokenUsagePricingConfigResponse,
    )
    def put_token_usage_pricing(
        payload: TokenUsagePricingConfigUpdate,
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> TokenUsagePricingConfigResponse:
        store.put(payload.model_dump())
        return TokenUsagePricingConfigResponse(
            currency=payload.currency,
            pricing=payload.pricing,
        )

    return router
