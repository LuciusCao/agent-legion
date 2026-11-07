"""Baseline and frozen-ceiling parsing for source-file budgets."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.quality.exemptions import ArchitectureExemption

from .budget_monotonicity import FrozenCeiling

__test__ = False


@dataclass(frozen=True)
class BudgetBaseline:
    files: dict[str, int]


class _BudgetConfigurationError(ValueError):
    """Internal configuration error captured by check_file_budgets."""

    pass


def load_budget_baseline(path: Path) -> BudgetBaseline:
    """Require exactly version 3 and a normalized positive ceiling map."""
    if not path.is_file():
        raise _BudgetConfigurationError(f"Baseline file not found: {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise _BudgetConfigurationError(f"Malformed JSON in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise _BudgetConfigurationError(
            f"Baseline root must be a mapping, got {type(raw).__name__}"
        )

    if set(raw) != {"version", "files"}:
        extra = set(raw) - {"version", "files"}
        missing = {"version", "files"} - set(raw)
        parts: list[str] = []
        if missing:
            parts.append(f"missing fields: {sorted(missing)}")
        if extra:
            parts.append(f"unknown fields: {sorted(extra)}")
        raise _BudgetConfigurationError(f"Invalid baseline structure; {'; '.join(parts)}")

    version = raw.get("version")
    if type(version) is not int or version != 3:
        raise _BudgetConfigurationError(f"Unsupported baseline version: {version!r}")

    files = raw.get("files")
    if not isinstance(files, dict):
        raise _BudgetConfigurationError("files must be a mapping")

    normalized: dict[str, int] = {}
    for key, value in files.items():
        if not isinstance(key, str):
            raise _BudgetConfigurationError("baseline path keys must be strings")
        if type(value) is not int:
            raise _BudgetConfigurationError(f"baseline ceiling for {key} must be an integer")
        if value <= 0:
            raise _BudgetConfigurationError(f"baseline ceiling for {key} must be positive")
        normalized_key = str(PurePosixPath(key))
        if normalized_key in normalized:
            raise _BudgetConfigurationError(f"duplicate normalized baseline path: {normalized_key}")
        normalized[normalized_key] = value

    return BudgetBaseline(files=normalized)


def _positive_int(value: Any) -> int:
    if type(value) is not int or isinstance(value, bool):
        raise _BudgetConfigurationError("ceiling must be a positive integer")
    if value <= 0:
        raise _BudgetConfigurationError("ceiling must be positive")
    return value


def _build_frozen_ceilings(
    production: tuple[str, ...],
    exemptions: tuple[ArchitectureExemption, ...],
) -> dict[str, FrozenCeiling]:
    """Build frozen ceiling map from file-budget exemptions."""
    frozen: dict[str, FrozenCeiling] = {}
    production_set = set(production)
    for ex in exemptions:
        if ex.check != "architecture.file_budget":
            continue
        if ex.path not in production_set:
            continue
        if ex.ceiling is None:
            continue
        frozen[ex.path] = FrozenCeiling(_positive_int(ex.ceiling), ex.expires)
    return frozen
