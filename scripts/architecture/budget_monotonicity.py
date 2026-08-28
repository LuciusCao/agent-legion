"""Monotonic (only-down) guard for budget ceilings (#209).

Budget ceilings are a one-way ratchet: an entry may stay or go down, never
up. The ratchet script itself never raises, but hand edits to
``architecture-budgets.json`` and hand-raised exemption ceilings previously
passed ``check_architecture`` untouched — the ratchet degenerated from
constraint into bookkeeping. Floor semantics live on
``ceiling_regression_errors``; new entries at actual + buffer_lines and
first-time dated exemptions stay unrestricted.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import yaml

__test__ = False

BUDGETS_RELATIVE_PATH = "config/architecture/architecture-budgets.json"
EXEMPTIONS_RELATIVE_PATH = "config/architecture/architecture-exemptions.yaml"

# env name is long but self-documenting; keep one line
_SHALLOW_OPT_OUT = "AGENT_LEGION_BUDGET_MONOTONICITY_SHALLOW"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr=str(exc))


def _committed_file_text(root: Path, revision: str, rel_path: str) -> str | None:
    """Content at a git revision, None when unavailable (non-git checkout,
    path predating the registry). A missing anchor never errors — the check
    only fires when an entry exists on both sides."""
    proc = _git(root, "show", f"{revision}:{rel_path}")
    return proc.stdout if proc.returncode == 0 else None


def _is_git_repository(root: Path) -> bool:
    return _git(root, "rev-parse", "--git-dir").returncode == 0


def _revision_resolvable(root: Path, revision: str) -> bool:
    """True when the anchor revision exists locally (not a shallow-clone hole)."""
    return _git(root, "rev-parse", "--verify", f"{revision}^{{commit}}").returncode == 0


def _unresolvable_anchor_errors(root: Path) -> list[str]:
    """Hard-fail on git checkouts whose anchors do not resolve: a shallow
    clone missing HEAD^ silently guts the committed-raise check exactly
    where CI gates PRs (codex review on PR #231). The env opt-out covers
    depth-1 checkouts that cannot fetch history; non-git checkouts stay
    quiet (nothing to compare against)."""
    if not _is_git_repository(root):
        return []
    errors: list[str] = []
    for revision in ("HEAD", "HEAD^"):
        if _revision_resolvable(root, revision):
            continue
        if os.environ.get(_SHALLOW_OPT_OUT) == "1":
            continue
        errors.append(
            f"budget monotonicity: git anchor {revision} does not resolve in this "
            "checkout (shallow clone?); fetch history (CI: fetch-depth: 0) or set "
            f"{_SHALLOW_OPT_OUT}=1 to deliberately skip the committed-raise check"
        )
    return errors


def _committed_budget_ceilings(text: str | None) -> dict[str, int]:
    """Path -> ceiling map from a committed budgets JSON (lenient: committed
    revisions may predate the current schema)."""
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
    """file_budget exemption ceilings from a committed registry YAML."""
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

    The min-across-anchors floor catches a raise introduced at any layer
    (uncommitted edit vs HEAD, smuggled-into-pending-commit vs HEAD^; on CI
    merge refs the first parent is the PR base, so the PR's own change is in
    view), while a working-tree revert of a committed raise passes. The
    anchor semantics live in ``_unresolvable_anchor_errors``.
    """
    errors = _unresolvable_anchor_errors(root)
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
