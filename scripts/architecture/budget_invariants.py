"""Budget-policy alignment for architecture exemption validation."""

from __future__ import annotations

from pathlib import Path

from scripts.quality.exemptions import ArchitectureExemption, validate_exemptions

from .budget_policy import load_budget_policy

__test__ = False


def validate_budget_exemptions(
    root: Path, exemptions: tuple[ArchitectureExemption, ...]
) -> list[str]:
    policy = load_budget_policy(root / "config/architecture/architecture-budget-policy.yaml")
    return validate_exemptions(exemptions, base_path=root, growth_allowance=policy.growth_allowance)
