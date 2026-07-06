from __future__ import annotations

from typing import Any

from server.app.services.token_usage_contracts import CostBreakdown


def load_pricing_config(config: dict[str, Any]) -> dict[tuple[str, str], dict[str, float]]:
    token_usage = config.get("token_usage", {})
    pricing = token_usage.get("pricing", [])
    return {
        (str(p["provider"]).strip(), str(p["model"]).strip()): {
            "input_per_1m": float(p["input_per_1m"]),
            "output_per_1m": float(p["output_per_1m"]),
            "cache_read_per_1m": float(p["cache_read_per_1m"]),
        }
        for p in pricing
        if "provider" in p and "model" in p
    }


def calculate_cost(
    total_tokens: int,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    provider: str,
    model: str,
    pricing_config: dict[str, Any],
) -> CostBreakdown:
    """Return a cost breakdown for the given usage and provider/model."""
    pricing = load_pricing_config(pricing_config)
    rates = pricing.get((provider.strip(), model.strip()))
    currency = str(pricing_config.get("token_usage", {}).get("currency", "")).strip()

    if rates is None:
        return CostBreakdown(
            currency=currency,
            input=0.0,
            output=0.0,
            cache_read=0.0,
            total=0.0,
            pricing_missing=True,
        )

    input_cost = input_tokens * rates["input_per_1m"] / 1_000_000
    output_cost = output_tokens * rates["output_per_1m"] / 1_000_000
    cache_read_cost = cache_read_tokens * rates["cache_read_per_1m"] / 1_000_000
    return CostBreakdown(
        currency=currency,
        input=input_cost,
        output=output_cost,
        cache_read=cache_read_cost,
        total=input_cost + output_cost + cache_read_cost,
        pricing_missing=False,
    )
