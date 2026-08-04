"""Bounded normalization for Worker capability and model declarations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def normalize_labels(labels: Mapping[str, Any]) -> dict[str, str]:
    if len(labels) > 32:
        raise ValueError("worker labels are capped at 32 entries")
    normalized: dict[str, str] = {}
    for key, value in labels.items():
        if not isinstance(key, str) or not key or len(key) > 64:
            raise ValueError("worker label keys must be non-empty strings up to 64 chars")
        if not isinstance(value, (str, int, float, bool)) or len(str(value)) > 256:
            raise ValueError(f"worker label {key!r} must have a bounded scalar value")
        normalized[key] = str(value)
    return normalized


def normalize_capabilities(values: Sequence[Any]) -> list[str]:
    if len(values) > 128:
        raise ValueError("worker capabilities are capped at 128 entries")
    normalized = sorted({str(value).strip() for value in values})
    if any(not value or value == "*" or len(value) > 128 for value in normalized):
        raise ValueError("worker capabilities must be non-empty strings up to 128 chars")
    return normalized


def normalize_models(values: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    if len(values) > 256:
        raise ValueError("worker models are capped at 256 entries")
    normalized: set[tuple[str, str]] = set()
    for item in values:
        provider = str(item.get("provider", "")).strip()
        model = str(item.get("model", "")).strip()
        if (
            not provider
            or not model
            or provider == "*"
            or model == "*"
            or len(provider) > 128
            or len(model) > 256
        ):
            raise ValueError("worker models require bounded provider and model strings")
        normalized.add((provider, model))
    return [{"provider": provider, "model": model} for provider, model in sorted(normalized)]


def normalize_worker_declarations(
    capabilities: Sequence[str] | None,
    models: Sequence[Mapping[str, Any]] | None,
) -> tuple[list[str], list[dict[str, str]]]:
    """Normalize HTTP declarations, retaining wildcard only for legacy direct callers."""
    normalized_capabilities = (
        ["*"] if capabilities is None else normalize_capabilities(capabilities)
    )
    normalized_models = (
        [{"provider": "*", "model": "*"}] if models is None else normalize_models(models)
    )
    return normalized_capabilities, normalized_models
