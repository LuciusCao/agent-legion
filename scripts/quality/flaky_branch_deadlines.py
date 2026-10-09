"""Flaky-registry deadline expiry on maintained non-default branches (#1024).

GitHub runs scheduled workflows on the default branch only, so the nightly
``check_reruns.py --check-deadlines`` never saw entries that exist only on
a ``release/*`` train. The PR gate catches entries a PR adds
or re-targets (``--base-registry``, #941 R3/R4, #1034); this job covers the
untouched rest by reading each maintained branch's registry from git.

"Maintained" is the minimal rule that needs no hand-kept list:

- every ``release/X.Y.Z`` whose version is ABOVE the version on the default
  branch (``pyproject.toml`` of the checkout) — a train at or below it has
  shipped into the default branch, whose own registry the nightly already
  checks; a ``release/*`` name that is not a version is kept (fail open
  towards checking, never towards skipping).

The ``develop`` branch was retired with the trunk-based migration (#1150);
only ``release/*`` trains remain as maintained non-default branches.

Branch registries are read leniently (``flaky_registry_lenient``): trains
cut before #941 have no ``registered_on`` and would fail the strict loader,
yet their deadlines still lapse. Only ``id`` / ``recurring`` / ``deadline``
matter here; schema validation is that branch's own PR gate's job.

The workflow fetches the refs first (``git fetch ... refs/heads/release/*``);
run locally after ``git fetch origin`` to reproduce.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tomllib
from datetime import date, timedelta
from pathlib import Path

from scripts.check_reruns import DEADLINE_WARNING_DAYS
from scripts.quality.flaky_registry import RegistryError
from scripts.quality.flaky_registry_lenient import lenient_entries

REGISTRY_PATH = "tests/flaky_registry.yaml"


def _version(text: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(part) for part in text.split("."))
    except ValueError:
        return None


def released_version(pyproject: Path) -> tuple[int, ...]:
    """The default branch's shipped version (``[project].version``)."""
    try:
        raw = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise RegistryError(f"cannot read the released version from {pyproject}: {exc}") from exc
    version = _version(str(raw))
    if version is None:
        raise RegistryError(f"{pyproject}: version {raw!r} is not dotted numeric")
    return version


def maintained_branches(branches: list[str], released: tuple[int, ...]) -> list[str]:
    """Filter ``release/*`` names down to the maintained ones."""
    selected: list[str] = []
    for name in sorted(set(branches)):
        if name.startswith("release/"):
            version = _version(name.removeprefix("release/"))
            if version is None or version > released:
                selected.append(name)
    return selected


def deadline_findings(
    entries: dict[str, dict], today: date
) -> tuple[list[tuple[str, date]], list[tuple[str, date]]]:
    """(expired, expiring within the warning horizon) as (id, deadline)."""
    expired: list[tuple[str, date]] = []
    soon: list[tuple[str, date]] = []
    horizon = today + timedelta(days=DEADLINE_WARNING_DAYS)
    for entry_id, raw in sorted(entries.items()):
        value = raw.get("deadline")
        if bool(raw.get("recurring", False)) or value is None:
            continue
        try:
            deadline = value if isinstance(value, date) else date.fromisoformat(str(value))
        except ValueError:
            expired.append((entry_id, date.min))  # unreadable deadline: never silently skip
            continue
        if deadline < today:
            expired.append((entry_id, deadline))
        elif deadline <= horizon:
            soon.append((entry_id, deadline))
    return expired, soon


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30, check=False
    )


def remote_branches(root: Path, remote: str) -> list[str]:
    prefix = f"refs/remotes/{remote}/"
    proc = _git(root, "for-each-ref", "--format=%(refname)", prefix + "release/")
    if proc.returncode != 0:
        raise RegistryError(f"git for-each-ref failed: {proc.stderr.strip()}")
    return [line.removeprefix(prefix) for line in proc.stdout.splitlines() if line]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--today", type=date.fromisoformat, default=None, metavar="YYYY-MM-DD")
    args = parser.parse_args(argv)
    today = args.today or date.today()
    annotate = os.environ.get("GITHUB_ACTIONS") == "true"

    failures: list[str] = []
    try:
        released = released_version(args.root / "pyproject.toml")
        branches = maintained_branches(remote_branches(args.root, args.remote), released)
    except RegistryError as exc:
        print(f"flaky branch deadlines error: {exc}", file=sys.stderr)
        return 1
    released_text = ".".join(map(str, released))
    print(f"Maintained branches (release/* above {released_text}, today {today}):")
    if not branches:
        print("  (none)")
    for branch in branches:
        ref = f"{args.remote}/{branch}"
        shown = _git(args.root, "show", f"{ref}:{REGISTRY_PATH}")
        if shown.returncode != 0:
            print(f"  {branch}: no {REGISTRY_PATH}; skipped")
            continue
        try:
            entries = lenient_entries(shown.stdout, f"{ref}:{REGISTRY_PATH}")
        except RegistryError as exc:
            failures.append(f"{branch}: {exc}")
            continue
        expired, soon = deadline_findings(entries, today)
        print(f"  {branch}: {len(entries)} entries, {len(expired)} expired")
        for entry_id, deadline in expired:
            failures.append(
                f"{branch}: {entry_id} deadline {deadline} expired; fix the flake or "
                "extend the entry on that branch with a reviewed reason"
            )
        for entry_id, deadline in soon:
            message = (
                f"{branch}: {entry_id} deadline {deadline} is within {DEADLINE_WARNING_DAYS} days"
            )
            print(f"  WARN: {message}")
            if annotate:
                print(f"::warning title=flaky registry deadline::{message}")
    if failures:
        print("\nViolations:")
        for failure in failures:
            print(f"  FAIL: {failure}")
            if annotate:
                print(f"::error title=flaky registry deadline::{failure}")
        return 1
    print("\nOK: no flaky registry deadline has expired on a maintained branch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
