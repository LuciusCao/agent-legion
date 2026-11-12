"""Strict typed loader for the source-file budget policy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .budget_policy_fields import (
    BudgetConfigurationError,
    _check_keys,
    _normalize_path,
    _parse_growth_allowance,
    _parse_positive_int,
    _parse_relative_path,
    _parse_relative_path_list,
    _parse_string_list,
    _require_mapping,
)

__test__ = False


@dataclass(frozen=True)
class ProductionRoot:
    path: str
    extensions: tuple[str, ...]
    max_lines: int | None = None


@dataclass(frozen=True)
class TestRoot:
    __test__ = False
    path: str
    patterns: tuple[str, ...]


@dataclass(frozen=True)
class BudgetPolicy:
    production_roots: tuple[ProductionRoot, ...]
    production_exclude: tuple[str, ...]
    buffer_lines: int
    production_max_lines: int
    test_roots: tuple[TestRoot, ...]
    test_max_lines: int
    # Growth allowance (#641): effective lines may exceed a registry
    # ceiling by this band without error, and the band is never absorbed
    # into the registry (the ratchet still only lowers) — unlike
    # buffer_lines, which the ratchet refills at actual + buffer.
    growth_allowance: int = 0
    per_file_ceiling_exempt_roots: tuple[str, ...] = ()


def per_file_ceiling_exempt_root(path: str, policy: BudgetPolicy) -> str | None:
    normalized = _normalize_path(path)
    for root in policy.per_file_ceiling_exempt_roots:
        if normalized == root or normalized.startswith(root + "/"):
            return root
    return None


def load_budget_policy(path: Path) -> BudgetPolicy:
    """Parse version 1 policy, rejecting unknown or unsafe values."""
    if not path.is_file():
        raise BudgetConfigurationError(f"Policy file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise BudgetConfigurationError(f"Malformed YAML in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise BudgetConfigurationError(f"Policy root must be a mapping, got {type(raw).__name__}")

    _check_keys(raw, {"version", "production", "tests"}, "policy root")

    version = raw.get("version")
    if type(version) is not int or version != 1:
        raise BudgetConfigurationError(f"Unsupported policy version: {version!r}")

    production = _require_mapping(raw, "production")
    tests = _require_mapping(raw, "tests")
    _check_keys(
        production,
        {
            "roots",
            "exclude",
            "buffer_lines",
            "max_lines",
            "growth_allowance",
            "per_file_ceiling_exempt_roots",
        },
        "production",
    )
    _check_keys(tests, {"roots", "max_lines"}, "tests")

    return BudgetPolicy(
        production_roots=_parse_production_roots(production),
        production_exclude=_parse_exclude(production),
        buffer_lines=_parse_positive_int(production, "buffer_lines"),
        production_max_lines=_parse_positive_int(production, "max_lines"),
        test_roots=_parse_test_roots(tests),
        test_max_lines=_parse_positive_int(tests, "max_lines"),
        growth_allowance=_parse_growth_allowance(production),
        per_file_ceiling_exempt_roots=_parse_relative_path_list(
            production, "per_file_ceiling_exempt_roots", "production"
        ),
    )


def _parse_exclude(production: dict[str, Any]) -> tuple[str, ...]:
    value = production.get("exclude", [])
    if not isinstance(value, list):
        raise BudgetConfigurationError("production.exclude must be a list")
    for item in value:
        if not isinstance(item, str):
            raise BudgetConfigurationError("production.exclude entries must be strings")
    return tuple(_normalize_path(item) for item in value)


def _parse_production_roots(production: dict[str, Any]) -> tuple[ProductionRoot, ...]:
    roots = production.get("roots", [])
    if not isinstance(roots, list):
        raise BudgetConfigurationError("production.roots must be a list")
    seen: set[str] = set()
    result: list[ProductionRoot] = []
    for idx, root in enumerate(roots):
        context = f"production.roots[{idx}]"
        _check_keys(root, {"path", "extensions", "max_lines"}, context)
        path = _parse_relative_path(root, "path", context)
        normalized = _normalize_path(path)
        if normalized in seen:
            raise BudgetConfigurationError(f"duplicate root path: {path}")
        seen.add(normalized)
        extensions = _parse_string_list(root, "extensions", context, non_empty=True)
        max_lines_raw = root.get("max_lines")
        max_lines: int | None = None
        if max_lines_raw is not None:
            if type(max_lines_raw) is not int or max_lines_raw <= 0:
                raise BudgetConfigurationError(f"{context}.max_lines must be a positive int")
            max_lines = max_lines_raw
        result.append(ProductionRoot(path=normalized, extensions=extensions, max_lines=max_lines))
    return tuple(result)


def _parse_test_roots(tests: dict[str, Any]) -> tuple[TestRoot, ...]:
    roots = tests.get("roots", [])
    if not isinstance(roots, list):
        raise BudgetConfigurationError("tests.roots must be a list")
    seen: set[str] = set()
    result: list[TestRoot] = []
    for idx, root in enumerate(roots):
        context = f"tests.roots[{idx}]"
        _check_keys(root, {"path", "patterns"}, context)
        path = _parse_relative_path(root, "path", context)
        normalized = _normalize_path(path)
        if normalized in seen:
            raise BudgetConfigurationError(f"duplicate root path: {path}")
        seen.add(normalized)
        patterns = _parse_string_list(root, "patterns", context, non_empty=True)
        result.append(TestRoot(path=normalized, patterns=patterns))
    return tuple(result)
