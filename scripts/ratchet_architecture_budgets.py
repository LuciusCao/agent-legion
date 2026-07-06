"""Safe ratchet for source-file architecture budgets: adds new, lowers stale, and removes obsolete entries; never raises an existing ceiling."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.architecture.budget_inventory import build_budget_inventory
from scripts.architecture.budget_policy import BudgetConfigurationError, load_budget_policy
from scripts.architecture.exemptions import load_exemptions
from scripts.architecture.file_budgets import (
    _BudgetConfigurationError,
    _build_frozen_ceilings,
    count_source_lines,
    load_budget_baseline,
)


@dataclass(frozen=True)
class RatchetResult:
    changed: bool
    errors: tuple[str, ...]


def ratchet_budgets(root: Path, *, rebase: bool = False) -> RatchetResult:
    """Add new, lower stale, and delete obsolete entries; never raise unless --rebase."""
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
        actual = count_source_lines(root / path)
        desired = actual + policy.buffer_lines
        existing = old_map.get(path)
        frozen = frozen_ceilings.get(path)
        # Rebase is not a pass to exceed the current ceiling: non-exempt
        # files are still checked against their existing baseline, and
        # exempt files against their frozen ceiling.
        effective_ceiling = frozen if frozen is not None else existing

        if effective_ceiling is not None and actual > effective_ceiling:
            errors.append(f"{path}: {actual} lines exceeds ceiling {effective_ceiling}; split the file or revert growth")  # fmt: skip
            continue

        if existing is None:
            new_map[path] = desired
        elif rebase and frozen is None:
            new_map[path] = desired if existing != desired else existing
        else:
            new_map[path] = min(existing, desired)

    if errors:
        return RatchetResult(changed=False, errors=tuple(errors))

    if new_map == old_map:
        return RatchetResult(changed=False, errors=())

    payload = {"version": 2, "files": dict(sorted(new_map.items()))}
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
    parser.add_argument("--rebase", action="store_true", help="Raise existing baseline ceilings to actual + buffer_lines (one-time use).")  # fmt: skip
    args = parser.parse_args(argv)
    result = ratchet_budgets(args.root.resolve(), rebase=args.rebase)
    for error in result.errors:
        print(f"ERROR: {error}")
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
