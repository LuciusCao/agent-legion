"""Parse committed budget/exemption registries at a git revision.

The monotonic check compares against how the registries looked at the HEAD
/ HEAD^ anchors. Committed revisions may predate the current schema, so
parsing is deliberately lenient: anything unreadable yields empty maps and
the anchor simply contributes no floor. The rename-floor carry also lives
here: a detected rename maps a new path onto the old path's committed floor.
"""

from __future__ import annotations

import json

import yaml

from .budget_git import BudgetGitUnavailable, GitHelper

__test__ = False

BUDGETS_RELATIVE_PATH = "config/architecture/architecture-budgets.json"
EXEMPTIONS_RELATIVE_PATH = "config/architecture/architecture-exemptions.yaml"


def committed_budget_ceilings(text: str | None) -> dict[str, int]:
    """Path -> ceiling map from a committed budgets JSON (lenient)."""
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


def committed_exemption_ceilings(text: str | None) -> dict[str, int]:
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


def rename_floor(
    git: GitHelper,
    path: str,
    budget_floors: dict[str, int],
    exemption_floors: dict[str, int],
) -> tuple[int, str] | None:
    """Old floor carried onto ``path`` when git detected a rename onto it.

    ``HEAD`` covers an uncommitted or staged rename, ``HEAD^`` a rename
    already committed into HEAD (the CI shape, where HEAD^ is the PR base).
    Carried floors follow the same min-across-anchors rule as ordinary
    floors, so a rename can only tighten, never loosen. A rename map that
    could not be built (untracked files + failed snapshot) raises
    ``BudgetGitUnavailable``: falling back to "no rename detected" would let
    an unstaged rename pass as a first-time registration (codex review on
    PR #238).
    """
    head_map = git.rename_map("HEAD")
    parent_map = git.rename_map("HEAD^")
    if head_map is None or parent_map is None:
        raise BudgetGitUnavailable(
            "budget monotonicity: rename detection could not run (worktree "
            "has untracked files and the snapshot index could not be built); "
            "failing closed rather than missing an unstaged rename"
        )
    old_path = head_map.get(path) or parent_map.get(path)
    if old_path is None or old_path == path:
        return None
    budget = budget_floors.get(old_path)
    exempt = exemption_floors.get(old_path)
    if exempt is None:
        return None if budget is None else (budget, old_path)
    return (exempt if budget is None else max(budget, exempt), old_path)


def effective_floor(
    git: GitHelper,
    path: str,
    floors: dict[str, int],
    budget_floors: dict[str, int],
    exemption_floors: dict[str, int],
) -> tuple[int | None, str | None]:
    """Minimum of the path-keyed floor and any rename-carried floor."""
    floor = floors.get(path)
    carried = rename_floor(git, path, budget_floors, exemption_floors)
    if carried is not None and (floor is None or carried[0] < floor):
        return carried
    return floor, None
