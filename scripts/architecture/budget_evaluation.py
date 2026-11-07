"""Effective-ceiling evaluation for source-file budgets."""

from __future__ import annotations

from pathlib import Path

from .budget_baseline import _positive_int
from .budget_monotonicity import FrozenCeiling
from .budget_policy import BudgetPolicy
from .effective_lines import count_effective_lines

__test__ = False


def stale_exemption_errors(
    root: Path,
    baseline_files: dict[str, int],
    frozen_ceilings: dict[str, FrozenCeiling],
    policy: BudgetPolicy,
) -> list[str]:
    errors: list[str] = []
    for path, entry in frozen_ceilings.items():
        file_path = root / path
        actual = count_effective_lines(file_path)

        normal_ceiling = baseline_files.get(path)
        if normal_ceiling is not None:
            normal_ceiling = _positive_int(normal_ceiling)
            if actual <= normal_ceiling and normal_ceiling <= actual + policy.buffer_lines:
                errors.append(
                    f"{path}: exemption ceiling {entry.ceiling} is stale; "
                    f"file fits within normal ceiling {normal_ceiling}; "
                    "remove the architecture.file_budget exemption"
                )
    return errors


def effective_ceiling_errors(
    root: Path,
    production: tuple[str, ...],
    baseline_files: dict[str, int],
    frozen_ceilings: dict[str, FrozenCeiling],
    policy: BudgetPolicy,
) -> list[str]:
    errors: list[str] = []
    for path in production:
        frozen = frozen_ceilings.get(path)
        effective_ceiling = frozen.ceiling if frozen is not None else baseline_files.get(path)
        if effective_ceiling is None:
            continue
        effective_ceiling = _positive_int(effective_ceiling)
        file_path = root / path
        actual = count_effective_lines(file_path)
        # #641 growth allowance: within-band overshoot passes and is never
        # absorbed into the registry — only beyond-band growth errors.
        allowance = policy.growth_allowance
        if actual > effective_ceiling + allowance:
            if allowance:
                errors.append(
                    f"{path}: {actual} effective lines exceeds ceiling {effective_ceiling} "
                    f"+ growth allowance {allowance}; split the file, re-file the "
                    "exemption, or revert growth"
                )
            else:
                errors.append(
                    f"{path}: {actual} effective lines exceeds ceiling {effective_ceiling}; "
                    "split the file or revert growth"
                )
        elif effective_ceiling > actual + policy.buffer_lines:
            errors.append(
                f"{path}: ceiling {effective_ceiling} is stale for {actual} effective lines; "
                "run scripts/ratchet_architecture_budgets.py"
            )
    return errors
