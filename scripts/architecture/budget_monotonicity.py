"""Monotonic (only-down) guard for budget ceilings (#209, #236).

Budget ceilings are a one-way ratchet: an entry may stay or go down, never
up. The ratchet script itself never raises, but hand edits to
``architecture-budgets.json`` and hand-raised exemption ceilings previously
passed ``check_architecture`` untouched — the ratchet degenerated from
constraint into bookkeeping. Floor semantics live on
``ceiling_regression_errors``: new entries at actual + buffer_lines and
first-time dated exemptions stay unrestricted, except that a rename detected
by git carries the old path's floor onto the new path (#236) — renaming a
file is not a ceiling reset button. Wording / git plumbing / registry
parsing live in ``budget_floor_errors`` / ``budget_git`` /
``budget_registry_history``.
"""

from __future__ import annotations

import os
from pathlib import Path

from .budget_floor_errors import baseline_raise_error, exemption_raise_error
from .budget_git import BudgetGitUnavailable, GitHelper
from .budget_registry_history import (
    BUDGETS_RELATIVE_PATH,
    EXEMPTIONS_RELATIVE_PATH,
    committed_budget_ceilings,
    committed_exemption_ceilings,
    effective_floor,
)

__test__ = False

_SHALLOW_OPT_OUT = "AGENT_LEGION_BUDGET_MONOTONICITY_SHALLOW"
_RELEASE_TRAIN_OPT_OUT = "AGENT_LEGION_BUDGET_MONOTONICITY_RELEASE_TRAIN"
_ANCHORS = ("HEAD", "HEAD^")


def _anchors() -> tuple[str, ...]:
    """Anchor revisions the working tree's ceilings must not rise above.

    The release train (develop→main) merges a head branch whose ceilings
    legitimately evolved over the whole release cycle: every raise passed
    this guard against develop's own anchors when its PR landed, so main's
    lagging floors reject already-reviewed history (0.4.0, PR #249 and its
    post-merge push run — the merge's first parent is the old, stale main).
    The CI workflow sets the opt-out for base=main && head=develop and for
    push runs on main/master; every other context keeps the HEAD^ anchor.
    """
    return ("HEAD",) if os.environ.get(_RELEASE_TRAIN_OPT_OUT) == "1" else _ANCHORS


def _unresolvable_anchor_errors(git: GitHelper) -> list[str]:
    """Hard-fail on git checkouts whose anchors do not resolve: a shallow
    clone missing HEAD^ silently guts the committed-raise check exactly
    where CI gates PRs (codex review on PR #231). The env opt-out covers
    depth-1 checkouts that cannot fetch history; non-git checkouts stay
    quiet (nothing to compare against). Git execution failures (missing
    binary, timeout, repository error) surface with their real reason
    instead of posing as shallow clones (#236)."""
    if not git.is_repository():
        if git.has_git_failures():
            return [f"budget monotonicity: git failed to run; cause: {git.diagnostics()}"]
        return []
    errors: list[str] = []
    for revision in _anchors():
        if git.revision_resolvable(revision) or os.environ.get(_SHALLOW_OPT_OUT) == "1":
            continue
        details = git.diagnostics()
        errors.append(
            f"budget monotonicity: git anchor {revision} does not resolve in this "
            "checkout (shallow clone / git error?); fetch history (CI: "
            f"fetch-depth: 0) or set {_SHALLOW_OPT_OUT}=1 to skip the check"
            + (f"; git failure: {details}" if details else "")
        )
    return errors


def _anchor_floors(git: GitHelper) -> tuple[dict[str, int], dict[str, int]]:
    """Effective-ceiling floors per path, minimum across committed anchors.

    Each anchor contributes max(baseline entry, exemption ceiling) per path —
    the effective ceiling a raise must not exceed. Taking the min across
    anchors catches a raise introduced at any layer (uncommitted edit vs
    HEAD, smuggled-into-pending-commit vs HEAD^; on CI merge refs the first
    parent is the PR base, so the PR's own change is in view), while a
    working-tree revert of a committed raise passes.
    """
    budget_floors: dict[str, int] = {}
    exemption_floors: dict[str, int] = {}
    for revision in _anchors():
        previous_budgets = committed_budget_ceilings(
            git.committed_file_text(revision, BUDGETS_RELATIVE_PATH)
        )
        previous_exemptions = committed_exemption_ceilings(
            git.committed_file_text(revision, EXEMPTIONS_RELATIVE_PATH)
        )
        for path, committed in previous_budgets.items():
            exempt = previous_exemptions.get(path)
            effective = committed if exempt is None else max(committed, exempt)
            if path not in budget_floors or effective < budget_floors[path]:
                budget_floors[path] = effective
        for path, committed in previous_exemptions.items():
            if path not in exemption_floors or committed < exemption_floors[path]:
                exemption_floors[path] = committed
    return budget_floors, exemption_floors


def ceiling_regression_errors(
    root: Path,
    baseline_files: dict[str, int],
    frozen_ceilings: dict[str, int],
) -> list[str]:
    """Reject ceiling increases against the committed monotonic floor.

    The floor is the minimum effective ceiling across the HEAD / HEAD^
    anchors, with renames carrying the old path's floor onto the new path
    (#236) — a rename is not a ceiling reset. New entries stay
    unrestricted unless a detected rename supplies their floor.
    """
    git = GitHelper(root)
    try:
        errors = _unresolvable_anchor_errors(git)
        if errors:
            # Anchors already failed (shallow clone / unborn HEAD): rename
            # detection cannot run against a missing revision either, and
            # the anchor error is the actionable one — do not mask it with
            # a rename-detection complaint.
            return errors
        budget_floors, exemption_floors = _anchor_floors(git)

        for path, ceiling in baseline_files.items():
            floor, origin = effective_floor(
                git, path, budget_floors, budget_floors, exemption_floors
            )
            if floor is not None and ceiling > floor:
                errors.append(baseline_raise_error(path, ceiling, floor, origin))

        for path, ceiling in frozen_ceilings.items():
            floor, _origin = effective_floor(
                git, path, exemption_floors, budget_floors, exemption_floors
            )
            if floor is not None and ceiling > floor:
                errors.append(exemption_raise_error(path, ceiling, floor))
        return errors
    except BudgetGitUnavailable as exc:
        # Fail closed: the snapshot path is the only way to see untracked
        # rename targets, so "cannot detect renames" must be an error, not
        # a silent no-renames answer (codex review on PR #238).
        return [str(exc)]
    finally:
        git.cleanup()
