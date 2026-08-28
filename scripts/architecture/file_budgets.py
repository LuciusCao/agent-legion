"""Source-file budget evaluator."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.quality.exemptions import ArchitectureExemption

from .budget_inventory import build_budget_inventory
from .budget_monotonicity import ceiling_regression_errors
from .budget_policy import BudgetPolicy
from .effective_lines import count_effective_lines

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


def count_source_lines(path: Path) -> int:
    """Raw line count, used for absolute size limits (not budget ceilings)."""
    return len(path.read_text(encoding="utf-8").splitlines())


def _positive_int(value: Any) -> int:
    if type(value) is not int or isinstance(value, bool):
        raise _BudgetConfigurationError("ceiling must be a positive integer")
    if value <= 0:
        raise _BudgetConfigurationError("ceiling must be positive")
    return value


def _build_frozen_ceilings(
    production: tuple[str, ...],
    exemptions: tuple[ArchitectureExemption, ...],
) -> dict[str, int]:
    """Build frozen ceiling map from file-budget exemptions."""
    frozen: dict[str, int] = {}
    production_set = set(production)
    for ex in exemptions:
        if ex.check != "architecture.file_budget":
            continue
        if ex.path not in production_set:
            continue
        if ex.ceiling is None:
            continue
        frozen[ex.path] = _positive_int(ex.ceiling)
    return frozen


def check_file_budgets(
    root: Path,
    policy: BudgetPolicy,
    exemptions: tuple[ArchitectureExemption, ...],
) -> list[str]:
    """Validate inventory, baseline completeness, limits, and frozen ceilings."""
    inventory, inventory_errors = build_budget_inventory(root, policy)
    if inventory_errors:
        return inventory_errors

    try:
        baseline = load_budget_baseline(root / "config/architecture/architecture-budgets.json")
    except _BudgetConfigurationError as exc:
        return [f"budget configuration: {exc}"]

    production_set = set(inventory.production)
    excluded_set = set(inventory.excluded)
    baseline_files = baseline.files
    errors: list[str] = []

    frozen_ceilings = _build_frozen_ceilings(inventory.production, exemptions)
    errors.extend(ceiling_regression_errors(root, baseline_files, frozen_ceilings))

    for path, ceiling in frozen_ceilings.items():
        file_path = root / path
        actual = count_effective_lines(file_path)

        normal_ceiling = baseline_files.get(path)
        if normal_ceiling is not None:
            normal_ceiling = _positive_int(normal_ceiling)
            if actual <= normal_ceiling and normal_ceiling <= actual + policy.buffer_lines:
                errors.append(
                    f"{path}: exemption ceiling {ceiling} is stale; "
                    f"file fits within normal ceiling {normal_ceiling}; "
                    "remove the architecture.file_budget exemption"
                )

    for path, ceiling in baseline_files.items():
        _positive_int(ceiling)
        if path in excluded_set:
            errors.append(
                f"{path}: stale baseline entry targets an excluded file; ratchet the baseline"
            )
        elif path not in production_set:
            errors.append(
                f"{path}: stale baseline entry targets a non-production file; ratchet the baseline"
            )

    for path in inventory.production:
        if path not in baseline_files and path not in frozen_ceilings:
            errors.append(
                f"{path}: production file has no baseline; "
                "run scripts/ratchet_architecture_budgets.py"
            )

    for path in inventory.production:
        actual = count_source_lines(root / path)
        if actual > policy.production_max_lines:
            errors.append(
                f"{path}: {actual} lines exceeds absolute production limit "
                f"{policy.production_max_lines}; exemptions do not apply; split the file"
            )

    for path in inventory.production:
        effective_ceiling = frozen_ceilings.get(path, baseline_files.get(path))
        if effective_ceiling is None:
            continue
        effective_ceiling = _positive_int(effective_ceiling)
        file_path = root / path
        actual = count_effective_lines(file_path)
        if actual > effective_ceiling:
            errors.append(
                f"{path}: {actual} effective lines exceeds ceiling {effective_ceiling}; "
                "split the file or revert growth"
            )
        elif effective_ceiling > actual + policy.buffer_lines:
            errors.append(
                f"{path}: ceiling {effective_ceiling} is stale for {actual} effective lines; "
                "run scripts/ratchet_architecture_budgets.py"
            )

    for path in inventory.tests:
        file_path = root / path
        actual = count_source_lines(file_path)
        if actual > policy.test_max_lines:
            errors.append(
                f"{path}: {actual} lines exceeds test limit {policy.test_max_lines}; "
                "split the test file"
            )

    return sorted(errors)
