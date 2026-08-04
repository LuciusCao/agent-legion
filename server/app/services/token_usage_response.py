from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from server.app.services.token_usage_pricing import calculate_cost


def currency_from_config(config: dict[str, Any]) -> str:
    return str(config.get("token_usage", {}).get("currency", "")).strip()


def _cost_breakdown(
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    total_tokens: int,
    provider: str,
    model: str,
    config: dict[str, Any],
) -> dict[str, Any] | None:
    cost = calculate_cost(
        total_tokens,
        input_tokens,
        output_tokens,
        cache_read_tokens,
        provider,
        model,
        config,
    )
    return cost.model_dump() if cost is not None else None


def build_aggregate_cost(
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    total_tokens: int,
    total_cost_value: float | None,
    pricing_missing: bool,
    usage_rows: Sequence[Mapping[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Build an aggregate cost object for a set of usage rows.

    When every row shares a single provider/model with a configured price, the
    full input/output/cache/total breakdown is returned. Otherwise only the
    total is exposed. If pricing is missing, ``cost`` is ``None``.
    """
    if not usage_rows:
        return {"cost": None, "pricing_missing": False}

    if pricing_missing:
        return {"cost": None, "pricing_missing": True}

    distinct = {
        (str(row.get("provider", "")), str(row.get("model", "")))
        for row in usage_rows
        if str(row.get("provider", "")) or str(row.get("model", ""))
    }
    if len(distinct) == 1:
        provider, model = next(iter(distinct))
        cost = _cost_breakdown(
            input_tokens,
            output_tokens,
            cache_read_tokens,
            total_tokens,
            provider,
            model,
            config,
        )
        if cost is not None:
            return {
                "cost": {
                    "currency": cost["currency"],
                    "input": cost["input"],
                    "output": cost["output"],
                    "cache_read": cost["cache_read"],
                    "total": cost["total"],
                },
                "pricing_missing": False,
            }

    return {
        "cost": {
            "currency": currency_from_config(config),
            "input": None,
            "output": None,
            "cache_read": None,
            "total": total_cost_value,
        },
        "pricing_missing": False,
    }


def usage_dict(row: Mapping[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    provider = str(row.get("provider", ""))
    model = str(row.get("model", ""))
    input_tokens = int(row.get("input_tokens", 0))
    output_tokens = int(row.get("output_tokens", 0))
    cache_read_tokens = int(row.get("cache_read_tokens", 0))
    total_tokens = int(row.get("total_tokens", 0))
    cost = _cost_breakdown(
        input_tokens,
        output_tokens,
        cache_read_tokens,
        total_tokens,
        provider,
        model,
        config,
    )
    pricing_missing = cost is None
    cost_obj: dict[str, Any] | None = None
    if cost is not None:
        cost_obj = {
            "currency": cost["currency"],
            "input": cost["input"],
            "output": cost["output"],
            "cache_read": cost["cache_read"],
            "total": cost["total"],
        }
    return {
        "node_run_id": int(row["node_run_id"]),
        "node_key": str(row.get("node_key", "")),
        "provider": provider,
        "model": model,
        "skill_version": str(row.get("skill_version", "")),
        "message_count": int(row.get("message_count", 0)),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "total_tokens": total_tokens,
        "cost": cost_obj,
        "pricing_missing": pricing_missing,
        "is_complete": bool(row.get("is_complete", 1)),
        "usage_source": str(row.get("usage_source", "events_jsonl")),
    }
