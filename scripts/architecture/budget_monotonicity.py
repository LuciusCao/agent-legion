"""Monotonic (only-down) guard for budget ceilings (#209, #236).

Budget ceilings are a one-way ratchet: an entry may stay or go down, never
up. The ratchet script itself never raises, but hand edits to
``architecture-budgets.json`` and hand-raised exemption ceilings previously
passed ``check_architecture`` untouched — the ratchet degenerated from
constraint into bookkeeping. Floor semantics live on
``ceiling_regression_errors``: new entries at actual + buffer_lines and
first-time dated exemptions stay unrestricted, except that a rename detected
by git carries the old path's floor onto the new path (#236) — renaming a
file is not a ceiling reset button. Anchors default to ``HEAD`` / ``HEAD^``;
``AGENT_LEGION_BUDGET_BASE`` replaces ``HEAD^`` with an explicit PR base so
a local run reproduces CI's merge-ref judgement (see ``budget_anchors``,
which also owns anchor wording and the per-anchor floor merge). Registry
parsing and the rename-floor carry live in ``budget_registry_history``.
"""

from __future__ import annotations

from pathlib import Path

from .budget_anchors import (
    anchor_budget_floors,
    anchor_revisions,
    base_anchor_override,
    base_floor_anchor,
    release_train_opt_out,
    shallow_opt_out,
    unresolvable_anchor_error,
    unresolvable_base_anchor_error,
)
from .budget_floor_errors import baseline_raise_error, exemption_raise_error
from .budget_git import BudgetGitUnavailable, GitHelper
from .budget_registry_history import effective_floor

__test__ = False


def _anchors() -> tuple[str, ...]:
    """Anchor revisions the working tree's ceilings must not rise above.

    The release train (develop→main) merges a head branch whose ceilings
    legitimately evolved over the whole release cycle: every raise passed
    this guard against develop's own anchors when its PR landed, so main's
    lagging floors reject already-reviewed history (0.4.0, PR #249 and its
    post-merge push run — the merge's first parent is the old, stale main).
    The CI workflow sets the opt-out for base=main && head=develop and for
    push runs on main/master; the opt-out takes precedence over the
    ``AGENT_LEGION_BUDGET_BASE`` override (ignoring the base's lagging
    floor is the release train's whole point). Every other context keeps
    the HEAD^ anchor, or the base override when configured.
    """
    return anchor_revisions(release_train=release_train_opt_out())


def _unresolvable_anchor_errors(git: GitHelper) -> list[str]:
    """Hard-fail on git checkouts whose anchors do not resolve: a shallow
    clone missing HEAD^ silently guts the committed-raise check exactly
    where CI gates PRs (codex review on PR #231). The env opt-out covers
    depth-1 checkouts that cannot fetch history — but never excuses an
    explicitly configured base ref, which must resolve or be fixed. Non-git
    checkouts stay quiet (nothing to compare against). Git execution
    failures (missing binary, timeout, repository error) surface with their
    real reason instead of posing as shallow clones (#236)."""
    if not git.is_repository():
        if git.has_git_failures():
            return [f"budget monotonicity: git failed to run; cause: {git.diagnostics()}"]
        return []
    errors: list[str] = []
    for revision in _anchors():
        if git.revision_resolvable(revision):
            continue
        if revision == base_anchor_override():
            errors.append(unresolvable_base_anchor_error("budget", revision))
        elif not shallow_opt_out():
            errors.append(unresolvable_anchor_error("budget", revision, git.diagnostics()))
    return errors


def ceiling_regression_errors(
    root: Path,
    baseline_files: dict[str, int],
    frozen_ceilings: dict[str, int],
) -> list[str]:
    """Reject ceiling increases against the committed monotonic floor.

    The floor is the minimum effective ceiling across the anchor revisions
    (HEAD / HEAD^ by default, HEAD / base with the override), with renames
    carrying the old path's floor onto the new path (#236) — a rename is
    not a ceiling reset. New entries stay unrestricted unless a detected
    rename supplies their floor.
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
        anchors = _anchors()
        budget_floors, exemption_floors, budget_sources, exemption_sources = anchor_budget_floors(
            git, anchors
        )

        for path, ceiling in baseline_files.items():
            floor, origin = effective_floor(
                git, path, budget_floors, budget_floors, exemption_floors, anchors
            )
            if floor is not None and ceiling > floor:
                errors.append(
                    baseline_raise_error(
                        path, ceiling, floor, origin, base_floor_anchor(budget_sources.get(path))
                    )
                )

        for path, ceiling in frozen_ceilings.items():
            floor, _origin = effective_floor(
                git, path, exemption_floors, budget_floors, exemption_floors, anchors
            )
            if floor is not None and ceiling > floor:
                errors.append(
                    exemption_raise_error(
                        path, ceiling, floor, base_floor_anchor(exemption_sources.get(path))
                    )
                )
        return errors
    except BudgetGitUnavailable as exc:
        # Fail closed: the snapshot path is the only way to see untracked
        # rename targets, so "cannot detect renames" must be an error, not
        # a silent no-renames answer (codex review on PR #238).
        return [str(exc)]
    finally:
        git.cleanup()
