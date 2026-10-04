"""Fail when pytest reruns hit tests outside the flaky registry.

The global ``--reruns 1`` gives a timing-sensitive test one diagnostic retry,
but a retry-pass must not become invisible. PR backend-coverage feeds every
``scripts/pytest_telemetry.py`` JSON report to this script and fails when a
rerun lands on a nodeid without a registry entry.

Deadline expiry is a wall-clock judgement, so it only fails the nightly
deadline-only mode (``--check-deadlines``, #941): PR runs and the unit tier
must stay deterministic, otherwise every entry reaching its deadline turns
unrelated PRs red on the same day. Rerun-report mode merely lists expired
entries. Deadline-only mode also warns ``DEADLINE_WARNING_DAYS`` ahead.

The registry schema and its clock-free rules (nodeid uniqueness, deadline
window) live in ``scripts/quality/flaky_registry.py``.
Registry: ``tests/flaky_registry.yaml``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Run as a file path from CI (`python scripts/check_reruns.py`): put the repo
# root on sys.path so the `scripts.quality` package resolves; idempotent when
# imported as `scripts.check_reruns`.
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.quality.flaky_registry import (  # noqa: E402  # sys.path first (above)
    MAX_DEADLINE_WINDOW_DAYS,
    RegistryEntry,
    RegistryError,
    load_registry,
)

__all__ = [
    "MAX_DEADLINE_WINDOW_DAYS",
    "RegistryEntry",
    "RegistryError",
    "evaluate",
    "expiring_soon",
    "load_registry",
    "load_rerun_nodeids",
    "main",
]

DEFAULT_REGISTRY = ROOT_DIR / "tests" / "flaky_registry.yaml"
DEADLINE_WARNING_DAYS = 7


def load_rerun_nodeids(paths: list[Path]) -> tuple[set[str], list[Path]]:
    """Collect rerun nodeids; missing/unreadable reports are skipped, not fatal."""
    nodeids: set[str] = set()
    missing: list[Path] = []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            tests = payload.get("tests", [])
        except (OSError, json.JSONDecodeError, AttributeError):
            missing.append(path)
            continue
        nodeids.update(str(test) for test in tests)
    return nodeids, missing


def evaluate(
    entries: list[RegistryEntry],
    rerun_nodeids: set[str],
    today: date,
    *,
    enforce_deadlines: bool = False,
) -> tuple[list[str], list[str]]:
    """Return (report lines, violations). Any violation means exit 1.

    Expired deadlines are violations only with ``enforce_deadlines`` (the
    nightly deadline-only mode); otherwise they are reported as notes so a
    calendar date can never red an unrelated PR (#941).
    """
    lines: list[str] = []
    violations: list[str] = []

    expired = [
        entry
        for entry in entries
        if not entry.recurring and entry.deadline is not None and entry.deadline < today
    ]
    for entry in expired:
        target = entry.nodeid or entry.scope
        message = (
            f"{entry.entry_id} ({target}): deadline {entry.deadline} expired; "
            "fix the flake or extend the entry with a reviewed reason"
        )
        if enforce_deadlines:
            violations.append(message)
        else:
            lines.append(f"  note: {message} (enforced by the nightly deadline check)")

    registered = {entry.nodeid: entry for entry in entries if entry.nodeid is not None}
    unregistered = sorted(nodeid for nodeid in rerun_nodeids if nodeid not in registered)
    for nodeid in unregistered:
        violations.append(f"rerun outside registry: {nodeid}")

    known = sorted(nodeid for nodeid in rerun_nodeids if nodeid in registered)
    lines.append(f"Rerun nodeids observed: {len(rerun_nodeids)}")
    for nodeid in known:
        entry = registered[nodeid]
        lines.append(f"  registered: {nodeid} ({entry.entry_id}, owner {entry.owner})")
    if not rerun_nodeids:
        lines.append("  (none)")
    lines.append(f"Registry entries: {len(entries)} ({len(expired)} expired)")
    return lines, violations


def expiring_soon(
    entries: list[RegistryEntry], today: date, days: int = DEADLINE_WARNING_DAYS
) -> list[RegistryEntry]:
    """Non-recurring entries whose deadline falls within ``days`` from today."""
    horizon = today + timedelta(days=days)
    return [
        entry
        for entry in entries
        if not entry.recurring and entry.deadline is not None and today <= entry.deadline <= horizon
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument(
        "--rerun-report",
        type=Path,
        action="append",
        default=[],
        metavar="PATH",
        help="pytest_telemetry rerun report; repeatable",
    )
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help="override the date used for deadline checks (testing)",
    )
    parser.add_argument(
        "--check-deadlines",
        action="store_true",
        help="deadline-only mode: no rerun report required (#295 — nightly "
        "exemption-expiry job detects an expired flaky registry deadline "
        "even when the extended rerun evidence did not run); the only mode "
        "in which an expired deadline fails (#941)",
    )
    args = parser.parse_args(argv)

    if not args.rerun_report and not args.check_deadlines:
        parser.error("at least one --rerun-report is required (or --check-deadlines)")

    today = args.today or date.today()
    try:
        entries = load_registry(args.registry)
    except RegistryError as exc:
        print(f"flaky registry error: {exc}", file=sys.stderr)
        return 1

    rerun_nodeids, missing = load_rerun_nodeids(args.rerun_report)
    lines, violations = evaluate(
        entries, rerun_nodeids, today, enforce_deadlines=args.check_deadlines
    )

    print(f"Flaky rerun governance (registry: {args.registry}, today: {today})")
    for line in lines:
        print(line)
    if args.check_deadlines:
        _warn_expiring(entries, today)
    for path in missing:
        print(f"note: skipped missing/unreadable rerun report {path}")
    if violations:
        print("\nViolations:")
        for violation in violations:
            print(f"  FAIL: {violation}")
        return 1
    if args.check_deadlines:
        print("\nOK: no flaky registry deadline has expired.")
    else:
        print("\nOK: all reruns are registered.")
    return 0


def _warn_expiring(entries: list[RegistryEntry], today: date) -> None:
    annotate = os.environ.get("GITHUB_ACTIONS") == "true"
    for entry in expiring_soon(entries, today):
        target = entry.nodeid or entry.scope
        message = (
            f"{entry.entry_id} ({target}) deadline {entry.deadline} is within "
            f"{DEADLINE_WARNING_DAYS} days; fix the flake or extend it with a reviewed reason"
        )
        print(f"  WARN: {message}")
        if annotate:
            print(f"::warning title=flaky registry deadline::{message}")


if __name__ == "__main__":
    raise SystemExit(main())
