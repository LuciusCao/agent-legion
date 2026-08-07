"""Safe ratchet for source-file architecture budgets.

Default mode adds new, lowers stale, and removes obsolete entries without
raising existing ceilings. The optional --rebase flag raises non-exempt
ceilings to actual + buffer_lines when the file is within its current ceiling.
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


def ratchet_budgets(
    root: Path,
    *,
    rebase: bool = False,
    bump: str | None = None,
) -> RatchetResult:
    """Ratchet budgets, with an explicit single-file ceiling bump channel."""
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
        # Rebase is not a free pass to exceed the current ceiling.
        # Non-exempt files are still checked against their existing baseline.
        # Exempt files are still checked against their frozen ceiling.
        # Only when the file is not currently over its ceiling may rebase
        # raise that ceiling up to actual + buffer_lines.
        effective_ceiling = frozen if frozen is not None else existing

        if bump is not None and path == bump:
            if frozen is not None:
                errors.append(
                    f"{path}: --bump does not apply to exempt files; remove the exemption first"
                )
                if existing is not None:
                    new_map[path] = existing
                continue
            new_map[path] = desired
            continue

        if effective_ceiling is not None and actual > effective_ceiling:
            errors.append(f"{path}: {actual} effective lines exceeds ceiling {effective_ceiling}; split the file or revert growth")  # fmt: skip
            if existing is not None:
                new_map[path] = existing
            continue

        if existing is None:
            new_map[path] = desired
        elif rebase and frozen is None:
            new_map[path] = desired if existing != desired else existing
        else:
            new_map[path] = min(existing, desired)

    if bump is not None and bump not in inventory.production:
        errors.append(f"{bump}: --bump target is not a production file")

    if errors and (bump is None or new_map == old_map):
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
    return RatchetResult(changed=True, errors=tuple(errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--rebase", action="store_true", help="Raise existing baseline ceilings to actual + buffer_lines (one-time use).")  # fmt: skip
    parser.add_argument(
        "--bump",
        metavar="PATH",
        default=None,
        help="Raise one repo-relative production file ceiling to actual + buffer_lines.",
    )
    args = parser.parse_args(argv)
    result = ratchet_budgets(args.root.resolve(), rebase=args.rebase, bump=args.bump)
    for error in result.errors:
        print(f"ERROR: {error}")
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
