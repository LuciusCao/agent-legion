"""Safe ratchet for source-file architecture budgets.

Adds new, lowers stale, and removes obsolete entries. Ceilings are a one-way
ratchet (#209): an existing entry is never raised, here or by hand —
``check_architecture`` rejects raises against the committed floor. The only
sanctioned way to raise a ceiling is a dated ``architecture.file_budget``
exemption in ``config/architecture/architecture-exemptions.yaml``, which
freezes a higher ceiling with removal tracking.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from scripts.architecture.budget_inventory import build_budget_inventory
from scripts.architecture.budget_policy import BudgetConfigurationError, load_budget_policy
from scripts.architecture.effective_lines import count_effective_lines
from scripts.architecture.exemptions import load_exemptions
from scripts.architecture.file_budgets import (
    _BudgetConfigurationError,
    _build_frozen_ceilings,
    load_budget_baseline,
)


@dataclass(frozen=True)
class RatchetResult:
    changed: bool
    errors: tuple[str, ...]


def ratchet_budgets(root: Path) -> RatchetResult:
    """Ratchet budgets down; never raise an existing ceiling."""
    errors: list[str] = []

    policy_path = root / "config/architecture/architecture-budget-policy.yaml"
    try:
        policy = load_budget_policy(policy_path)
    except BudgetConfigurationError as exc:
        return RatchetResult(changed=False, errors=(f"budget configuration: {exc}",))

    inventory, inventory_errors = build_budget_inventory(root, policy)
    if inventory_errors:
        return RatchetResult(changed=False, errors=tuple(inventory_errors))

    baseline_path = root / "config/architecture/architecture-budgets.json"
    try:
        baseline = load_budget_baseline(baseline_path)
    except _BudgetConfigurationError as exc:
        return RatchetResult(changed=False, errors=(f"budget configuration: {exc}",))

    old_map = baseline.files
    new_map: dict[str, int] = {}
    try:
        frozen_ceilings = _build_frozen_ceilings(inventory.production, load_exemptions(root))
    except _BudgetConfigurationError as exc:
        return RatchetResult(changed=False, errors=(f"budget configuration: {exc}",))

    for path in inventory.production:
        actual = count_effective_lines(root / path)
        desired = actual + policy.buffer_lines
        existing = old_map.get(path)
        frozen = frozen_ceilings.get(path)
        # Exempt files answer to their frozen exemption ceiling instead of the
        # baseline entry, which stays untouched for when the exemption is
        # removed.
        effective_ceiling = frozen if frozen is not None else existing

        if effective_ceiling is not None and actual > effective_ceiling:
            errors.append(f"{path}: {actual} effective lines exceeds ceiling {effective_ceiling}; split the file or revert growth")  # fmt: skip
            if existing is not None:
                new_map[path] = existing
            continue

        if existing is None:
            new_map[path] = desired
        else:
            new_map[path] = min(existing, desired)

    if errors:
        return RatchetResult(changed=False, errors=tuple(errors))

    if new_map == old_map:
        return RatchetResult(changed=False, errors=())

    payload = {"version": 3, "files": dict(sorted(new_map.items()))}
    text = json.dumps(payload, indent=2, sort_keys=False) + "\n"

    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=baseline_path.parent, suffix=".tmp", delete=False
    ) as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
        tmp_path = f.name

    os.replace(tmp_path, baseline_path)
    return RatchetResult(changed=True, errors=())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    result = ratchet_budgets(args.root.resolve())
    for error in result.errors:
        print(f"ERROR: {error}")
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
