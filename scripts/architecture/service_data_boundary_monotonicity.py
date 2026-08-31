"""Monotonic (only-down) guard for the service data-boundary baseline (#292).

The boundary ratchet counted bypasses against the baseline but never
compared across revisions: hand-adding a baseline entry (or raising a
count) passed ``check_architecture`` untouched, so the governance strength
depended on review discipline instead of the machine — the git history
shows the add-entry-to-pass channel was actually used (60 → 67 → 56 over
one week). This module ports the budget-monotonicity design
(``budget_monotonicity.py``, #209) to the boundary baseline:

- the set of baseline paths only shrinks — a NEW entry means a new bypass
  surface and needs an explicit exemption channel, not a silent edit;
- per-path triple counts ``(sql, primitives, dsn)`` only ratchet down.

Anchors are HEAD / HEAD^ (same rationale as budgets: an uncommitted edit
compares against HEAD, an edit already staged/committed into the pending
commit compares against HEAD^; on CI merge refs HEAD^ is the PR base), and
``AGENT_LEGION_BUDGET_BASE`` replaces HEAD^ with an explicit PR base so a
local run reproduces CI's merge-ref judgement (shared plumbing in
``budget_anchors``). Shallow clones and non-git checkouts follow the budget
guard's rules: unresolvable anchors hard-fail (with the same opt-out env,
which never excuses an explicitly configured base ref), a non-git
directory stays quiet.
"""

from __future__ import annotations

from pathlib import Path

from .budget_anchors import (
    anchor_revisions,
    base_anchor_override,
    shallow_opt_out,
    unresolvable_anchor_error,
    unresolvable_base_anchor_error,
)
from .budget_git import BudgetGitUnavailable, GitHelper
from .service_data_boundary_history import (
    boundary_floors_and_history,
)

__test__ = False


def _anchors() -> tuple[str, ...]:
    """HEAD / HEAD^, or HEAD + the AGENT_LEGION_BUDGET_BASE override."""
    return anchor_revisions(release_train=False)


def _unresolvable_anchor_errors(git: GitHelper) -> list[str]:
    """Hard-fail on checkouts whose anchors do not resolve (shallow clone)."""
    if not git.is_repository():
        if git.has_git_failures():
            return [f"boundary monotonicity: git failed to run; cause: {git.diagnostics()}"]
        return []
    errors: list[str] = []
    for revision in _anchors():
        if git.revision_resolvable(revision):
            continue
        if revision == base_anchor_override():
            errors.append(unresolvable_base_anchor_error("boundary", revision))
        elif not shallow_opt_out():
            errors.append(unresolvable_anchor_error("boundary", revision, git.diagnostics()))
    return errors


def boundary_regression_errors(
    root: Path, baseline_files: dict[str, tuple[int, int, int]]
) -> list[str]:
    """Reject baseline growth against the committed monotonic floor.

    The floor per path is the entry-wise minimum across the anchor
    revisions; a path absent from the pre-change anchor's baseline is a
    first-time registration — rejected outright, because a NEW service with
    bypasses violates BOUNDARY-DATA-001 the same as an old one growing debt
    (the plain no-entry check would catch the file, but a same-commit
    file + entry registration passes it; codex review round 2 on #305).
    Renames carry the old path's floor onto the new path (#236 semantics):
    renaming a service file is not a boundary-count reset button.
    """
    git = GitHelper(root)
    try:
        if not git.is_repository():
            # Non-git checkouts have no committed anchor to compare
            # against; the guard stays quiet (mirrors the budget guard).
            return []
        errors = _unresolvable_anchor_errors(git)
        if errors:
            return errors
        floors, historic_paths = boundary_floors_and_history(git, _anchors())

        for path, triple in baseline_files.items():
            if path not in historic_paths:
                # ANY first-time entry against the pre-change baseline is a
                # new bypass surface, for a new file as much as an old one:
                # AGENTS.md requires NEW services to reach the database
                # through JobQueries too, so a brand-new service file with
                # bypasses is not a legitimate first registration — it is
                # the debt arriving in the same commit as its file (codex
                # review round 2 on #305).
                errors.append(
                    f"{path}: baseline entry appeared without pre-change "
                    "history; new services must reach the database through "
                    "JobQueries (BOUNDARY-DATA-001), not start with bypasses"
                )
                continue
            floor = floors[path]
            if any(a > b for a, b in zip(triple, floor, strict=True)):
                errors.append(
                    f"{path}: baseline triple {list(triple)} rose above committed "
                    f"floor {list(floor)}; boundary counts only ratchet down — "
                    "route new DB access through JobQueries instead"
                )
        return errors
    except BudgetGitUnavailable as exc:
        # Fail closed, mirroring the budget guard: an unavailable git
        # surface must not silently disable the anchor comparison.
        return [str(exc)]
    finally:
        git.cleanup()


def _path_exists_in_revision(git: GitHelper, revision: str, path: str) -> bool:
    """True when ``path`` exists in ``revision``'s tree (any content)."""
    result = git.run("cat-file", "-e", f"{revision}:{path}")
    return result is not None and result.returncode == 0
