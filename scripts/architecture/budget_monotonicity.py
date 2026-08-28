"""Monotonic (only-down) guard for budget ceilings (#209).

Budget ceilings are a one-way ratchet: an entry may stay or go down, never
up. The ratchet script itself never raises, but hand edits to
``architecture-budgets.json`` and hand-raised exemption ceilings previously
passed ``check_architecture`` untouched — the ratchet degenerated from a
constraint into bookkeeping.

This module compares the working-tree registries against their recent
committed state. The floor for each file is the lowest effective ceiling
recorded at any anchor (HEAD, HEAD^), so a raise is caught whichever layer
it was introduced at, while a working-tree fix reverting a committed raise
passes. A committed effective ceiling is the higher of a file's baseline
entry and its exemption ceiling, so retiring an exemption onto a baseline
entry at or below the frozen value is a tightening, not a raise. New entries
(newly registered files, newly filed exemptions) are unrestricted:
registering at actual + buffer_lines is the sanctioned way a ceiling
appears, and anything beyond that must go through a dated
``architecture.file_budget`` exemption (``remove_when`` + 30-day age
reporting in ``scripts/quality/exemption_age.py``).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

__test__ = False

BUDGETS_RELATIVE_PATH = "config/architecture/architecture-budgets.json"
EXEMPTIONS_RELATIVE_PATH = "config/architecture/architecture-exemptions.yaml"


def _committed_file_text(root: Path, revision: str, rel_path: str) -> str | None:
    """Return a governed file's content at a git revision, None if unavailable.

    Unavailable covers non-git checkouts (tests, tarballs), missing paths at
    that revision, and shallow clones whose parents are not fetched. A missing
    anchor never produces errors — the monotonic check only fires when an
    entry exists on both sides.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "show", f"{revision}:{rel_path}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _committed_budget_ceilings(text: str | None) -> dict[str, int]:
    """Extract the path -> ceiling map from a committed budgets JSON.

    Deliberately lenient: committed revisions may predate the current schema,
    so only the ``files`` mapping of positive integer ceilings is read.
    """
    if text is None:
        return {}
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return {}
    files = raw.get("files") if isinstance(raw, dict) else None
    if not isinstance(files, dict):
        return {}
    return {
        key: value
        for key, value in files.items()
        if isinstance(key, str) and type(value) is int and value > 0
    }


def _committed_exemption_ceilings(text: str | None) -> dict[str, int]:
    """Extract file_budget exemption ceilings from a committed registry YAML."""
    if text is None:
        return {}
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError:
        return {}
    entries = raw.get("exemptions") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        return {}
    ceilings: dict[str, int] = {}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("check") != "architecture.file_budget":
            continue
        path = entry.get("path")
        ceiling = entry.get("ceiling")
        if isinstance(path, str) and type(ceiling) is int and ceiling > 0:
            ceilings[path] = ceiling
    return ceilings


def ceiling_regression_errors(
    root: Path,
    baseline_files: dict[str, int],
    frozen_ceilings: dict[str, int],
) -> list[str]:
    """Reject ceiling increases against the committed monotonic floor.

    The floor for each file is the lowest effective ceiling recorded at any
    recent anchor revision (HEAD, HEAD^). Taking the minimum means a raise is
    caught no matter which layer it was introduced at — an uncommitted manual
    edit against HEAD, or a raise already smuggled into the pending commit
    against HEAD^ (on CI merge refs the first parent is the PR base branch,
    so the PR's own change is in view) — while a working-tree fix that
    reverts an already-committed raise to the older, lower value passes.
    """
    errors: list[str] = []
    budget_floors: dict[str, int] = {}
    exemption_floors: dict[str, int] = {}
    for revision in ("HEAD", "HEAD^"):
        previous_budgets = _committed_budget_ceilings(
            _committed_file_text(root, revision, BUDGETS_RELATIVE_PATH)
        )
        previous_exemptions = _committed_exemption_ceilings(
            _committed_file_text(root, revision, EXEMPTIONS_RELATIVE_PATH)
        )
        for path, committed in previous_budgets.items():
            exempt = previous_exemptions.get(path)
            effective = committed if exempt is None else max(committed, exempt)
            if path not in budget_floors or effective < budget_floors[path]:
                budget_floors[path] = effective
        for path, committed in previous_exemptions.items():
            if path not in exemption_floors or committed < exemption_floors[path]:
                exemption_floors[path] = committed

    for path, ceiling in baseline_files.items():
        floor = budget_floors.get(path)
        if floor is not None and ceiling > floor:
            errors.append(
                f"{path}: ceiling {ceiling} rose above committed ceiling "
                f"{floor}; ceilings only ratchet down — split the file or "
                "file a dated architecture.file_budget exemption"
            )

    for path, ceiling in frozen_ceilings.items():
        floor = exemption_floors.get(path)
        if floor is not None and ceiling > floor:
            errors.append(
                f"{path}: exemption ceiling {ceiling} rose above committed "
                f"ceiling {floor}; exemption ceilings only ratchet down — "
                "re-file the exemption or split the file"
            )
    return errors
