"""Source-file budget evaluator."""

from __future__ import annotations

from pathlib import Path

from scripts.quality.exemptions import ArchitectureExemption

from .budget_baseline import BudgetBaseline as BudgetBaseline
from .budget_baseline import (
    _BudgetConfigurationError,
    _build_frozen_ceilings,
    load_budget_baseline,
)
from .budget_evaluation import effective_ceiling_errors, stale_exemption_errors
from .budget_inventory import absolute_limit_map, build_budget_inventory
from .budget_monotonicity import ceiling_regression_errors
from .budget_policy import BudgetPolicy

__test__ = False


def count_source_lines(path: Path) -> int:
    """Raw line count, used for absolute size limits (not budget ceilings)."""
    return len(path.read_text(encoding="utf-8").splitlines())


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
    errors.extend(
        ceiling_regression_errors(
            root, baseline_files, frozen_ceilings, growth_allowance=policy.growth_allowance
        )
    )

    errors.extend(stale_exemption_errors(root, baseline_files, frozen_ceilings, policy))

    for path in baseline_files:
        if path in excluded_set:
            errors.append(
                f"{path}: stale baseline entry targets an excluded file; ratchet the baseline"
            )
        elif path not in production_set:
            errors.append(
                f"{path}: stale baseline entry targets a non-production file; ratchet the baseline"
            )

    # #293: per-root max_lines overrides (declarative artifacts like the
    # full-replay schema file) replace the global absolute limit.
    limits = absolute_limit_map(policy, inventory.production)
    for path in inventory.production:
        if path not in baseline_files and path not in frozen_ceilings:
            errors.append(
                f"{path}: production file has no baseline; "
                "run scripts/ratchet_architecture_budgets.py"
            )
        actual = count_source_lines(root / path)
        limit = limits.get(path, policy.production_max_lines)
        if actual > limit:
            errors.append(
                f"{path}: {actual} lines exceeds absolute production limit "
                f"{limit}; exemptions do not apply; split the file"
            )

    errors.extend(
        effective_ceiling_errors(
            root, inventory.production, baseline_files, frozen_ceilings, policy
        )
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
