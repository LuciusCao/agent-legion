"""Shared scalar, mapping, and path parsers for the budget policy."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

__test__ = False


class BudgetConfigurationError(ValueError):
    """Raised when a budget policy file is missing or invalid."""

    pass


def _parse_growth_allowance(production: dict[str, Any]) -> int:
    """Optional non-negative band; absent (or 0) keeps the strict legacy line."""
    if "growth_allowance" not in production:
        return 0
    value = production["growth_allowance"]
    if type(value) is not int:
        raise BudgetConfigurationError("growth_allowance must be an integer")
    if value < 0:
        raise BudgetConfigurationError("growth_allowance must not be negative")
    return value


def _check_keys(mapping: Any, allowed: set[str], context: str) -> None:
    if not isinstance(mapping, dict):
        raise BudgetConfigurationError(f"{context} must be a mapping")
    extra = set(mapping) - allowed
    if extra:
        plural = "s" if len(extra) > 1 else ""
        raise BudgetConfigurationError(f"unknown field{plural} in {context}: {sorted(extra)}")


def _require_mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw[key]
    if not isinstance(value, dict):
        raise BudgetConfigurationError(f"{key} must be a mapping")
    return value


def _parse_positive_int(mapping: dict[str, Any], key: str) -> int:
    if key not in mapping:
        raise BudgetConfigurationError(f"{key} is required")
    value = mapping[key]
    if type(value) is not int:
        raise BudgetConfigurationError(f"{key} must be an integer")
    if value <= 0:
        raise BudgetConfigurationError(f"{key} must be positive")
    return value


def _parse_relative_path(root: dict[str, Any], key: str, context: str) -> str:
    if key not in root:
        raise BudgetConfigurationError(f"{context}.{key} is required")
    value = root[key]
    if not isinstance(value, str):
        raise BudgetConfigurationError(f"{context}.{key} must be a string")
    if value.startswith("/"):
        raise BudgetConfigurationError(f"{context}.{key} must be a relative path: {value}")
    if ".." in PurePosixPath(value).parts:
        raise BudgetConfigurationError(f"{context}.{key} must not contain '..': {value}")
    return value


def _normalize_path(value: str) -> str:
    return str(PurePosixPath(value))


def _parse_string_list(
    root: dict[str, Any], key: str, context: str, *, non_empty: bool
) -> tuple[str, ...]:
    if key not in root:
        raise BudgetConfigurationError(f"{context}.{key} is required")
    value = root[key]
    if not isinstance(value, list):
        raise BudgetConfigurationError(f"{context}.{key} must be a list")
    if non_empty and len(value) == 0:
        raise BudgetConfigurationError(f"{context}.{key} must not be empty")
    for item in value:
        if not isinstance(item, str):
            raise BudgetConfigurationError(f"{context}.{key} entries must be strings")
    return tuple(value)
