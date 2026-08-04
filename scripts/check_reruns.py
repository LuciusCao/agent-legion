"""Fail when pytest reruns hit tests outside the flaky registry.

Nightly-only governance (test architecture plan, Phase 5D): the global
``--reruns 1`` absorbs flakes silently on PR lanes. This script turns rerun
evidence (``scripts/pytest_telemetry.py`` JSON reports) into a failure when a
rerun lands on a nodeid that has no registry entry, or when a non-recurring
registry entry outlives its deadline. Registry: ``tests/flaky_registry.yaml``.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = ROOT_DIR / "tests" / "flaky_registry.yaml"


class RegistryError(ValueError):
    """Raised when the flaky registry fails schema validation."""


@dataclass(frozen=True)
class RegistryEntry:
    entry_id: str
    owner: str
    reason: str
    observed: str
    nodeid: str | None
    scope: str | None
    deadline: date | None
    recurring: bool


def _parse_entry(raw: object, index: int) -> RegistryEntry:
    where = f"entries[{index}]"
    if not isinstance(raw, dict):
        raise RegistryError(f"{where}: entry must be a mapping")

    entry_id = raw.get("id")
    if not isinstance(entry_id, str) or not entry_id.strip():
        raise RegistryError(f"{where}: missing or invalid 'id'")
    where = f"entry {entry_id}"

    for field in ("owner", "reason", "observed"):
        if not isinstance(raw.get(field), str) or not str(raw[field]).strip():
            raise RegistryError(f"{where}: missing or invalid '{field}'")

    nodeid = raw.get("nodeid")
    scope = raw.get("scope")
    if (nodeid is None) == (scope is None):
        raise RegistryError(f"{where}: exactly one of 'nodeid' or 'scope' is required")
    for name, value in (("nodeid", nodeid), ("scope", scope)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise RegistryError(f"{where}: '{name}' must be a non-empty string")

    recurring = bool(raw.get("recurring", False))
    raw_deadline = raw.get("deadline")
    if recurring:
        if raw_deadline is not None:
            raise RegistryError(f"{where}: recurring entries must not set 'deadline'")
        deadline = None
    else:
        if raw_deadline is None:
            raise RegistryError(f"{where}: non-recurring entries require 'deadline'")
        try:
            deadline = date.fromisoformat(str(raw_deadline))
        except ValueError as exc:
            raise RegistryError(f"{where}: invalid 'deadline' {raw_deadline!r}") from exc

    return RegistryEntry(
        entry_id=entry_id.strip(),
        owner=str(raw["owner"]).strip(),
        reason=str(raw["reason"]).strip(),
        observed=str(raw["observed"]).strip(),
        nodeid=nodeid.strip() if isinstance(nodeid, str) else None,
        scope=scope.strip() if isinstance(scope, str) else None,
        deadline=deadline,
        recurring=recurring,
    )


def load_registry(path: Path) -> list[RegistryEntry]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RegistryError(f"cannot read registry {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise RegistryError(f"{path}: top-level 'entries' list is required")
    entries = [_parse_entry(raw, index) for index, raw in enumerate(data["entries"])]
    seen: set[str] = set()
    for entry in entries:
        if entry.entry_id in seen:
            raise RegistryError(f"duplicate entry id {entry.entry_id}")
        seen.add(entry.entry_id)
    return entries


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
) -> tuple[list[str], list[str]]:
    """Return (report lines, violations). Any violation means exit 1."""
    lines: list[str] = []
    violations: list[str] = []

    expired = [
        entry
        for entry in entries
        if not entry.recurring and entry.deadline is not None and entry.deadline < today
    ]
    for entry in expired:
        target = entry.nodeid or entry.scope
        violations.append(
            f"{entry.entry_id} ({target}): deadline {entry.deadline} expired; "
            "fix the flake or extend the entry with a reviewed reason"
        )

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
    args = parser.parse_args(argv)

    if not args.rerun_report:
        parser.error("at least one --rerun-report is required")

    today = args.today or date.today()
    try:
        entries = load_registry(args.registry)
    except RegistryError as exc:
        print(f"flaky registry error: {exc}", file=sys.stderr)
        return 1

    rerun_nodeids, missing = load_rerun_nodeids(args.rerun_report)
    lines, violations = evaluate(entries, rerun_nodeids, today)

    print(f"Flaky rerun governance (registry: {args.registry}, today: {today})")
    for line in lines:
        print(line)
    for path in missing:
        print(f"note: skipped missing/unreadable rerun report {path}")
    if violations:
        print("\nViolations:")
        for violation in violations:
            print(f"  FAIL: {violation}")
        return 1
    print("\nOK: all reruns are registered and no deadline has expired.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
