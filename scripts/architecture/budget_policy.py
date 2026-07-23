"""Strict typed loader for the source-file budget policy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

__test__ = False


class BudgetConfigurationError(ValueError):
    """Raised when a budget policy file is missing or invalid."""

    pass


@dataclass(frozen=True)
class ProductionRoot:
    path: str
    extensions: tuple[str, ...]


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
    _check_keys(production, {"roots", "exclude", "buffer_lines", "max_lines"}, "production")
    _check_keys(tests, {"roots", "max_lines"}, "tests")

    return BudgetPolicy(
        production_roots=_parse_production_roots(production),
        production_exclude=_parse_exclude(production),
        buffer_lines=_parse_positive_int(production, "buffer_lines"),
        production_max_lines=_parse_positive_int(production, "max_lines"),
        test_roots=_parse_test_roots(tests),
        test_max_lines=_parse_positive_int(tests, "max_lines"),
    )


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
        _check_keys(root, {"path", "extensions"}, context)
        path = _parse_relative_path(root, "path", context)
        normalized = _normalize_path(path)
        if normalized in seen:
            raise BudgetConfigurationError(f"duplicate root path: {path}")
        seen.add(normalized)
        extensions = _parse_string_list(root, "extensions", context, non_empty=True)
        result.append(ProductionRoot(path=normalized, extensions=extensions))
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
