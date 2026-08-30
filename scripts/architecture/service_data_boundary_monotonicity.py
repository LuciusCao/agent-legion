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
commit compares against HEAD^; on CI merge refs HEAD^ is the PR base).
Shallow clones and non-git checkouts follow the budget guard's rules:
unresolvable anchors hard-fail (with the same opt-out env), a non-git
directory stays quiet.
"""

from __future__ import annotations

import os
from pathlib import Path

from .budget_git import BudgetGitUnavailable, GitHelper
from .service_data_boundary_history import (
    boundary_floors_and_history,
)

__test__ = False


_SHALLOW_OPT_OUT = "AGENT_LEGION_BUDGET_MONOTONICITY_SHALLOW"
_ANCHORS = ("HEAD", "HEAD^")


def _unresolvable_anchor_errors(git: GitHelper) -> list[str]:
    """Hard-fail on checkouts whose anchors do not resolve (shallow clone)."""
    if not git.is_repository():
        if git.has_git_failures():
            return [f"boundary monotonicity: git failed to run; cause: {git.diagnostics()}"]
        return []
    errors: list[str] = []
    for revision in _ANCHORS:
        if git.revision_resolvable(revision) or os.environ.get(_SHALLOW_OPT_OUT) == "1":
            continue
        details = git.diagnostics()
        errors.append(
            f"boundary monotonicity: git anchor {revision} does not resolve in this "
            "checkout (shallow clone / git error?); fetch history (CI: "
            f"fetch-depth: 0) or set {_SHALLOW_OPT_OUT}=1 to skip the check"
            + (f"; git failure: {details}" if details else "")
        )
    return errors


def boundary_regression_errors(
    root: Path, baseline_files: dict[str, tuple[int, int, int]]
) -> list[str]:
    """Reject baseline growth against the committed monotonic floor.

    The floor per path is the entry-wise minimum across the anchor
    revisions; a path absent from every anchor is a first-time registration
    (legitimate only for a brand-new service file, which the plain
    no-entry check of ``check_service_data_boundary`` already governs —
    entries here exist because the file carries bypasses, so a new entry
    appearing while the file itself is not new means someone grew debt).
    Renames carry the old path's floor onto the new path (#236 semantics):
    renaming a service file is not a boundary-count reset button.
    """
    git = GitHelper(root)
    try:
        errors = _unresolvable_anchor_errors(git)
        if errors:
            return errors
        floors, historic_paths = boundary_floors_and_history(git)

        for path, triple in baseline_files.items():
            if path not in historic_paths:
                # First-time entry against the pre-change baseline. A
                # brand-new service file legitimately starts here; a service
                # file that already existed at HEAD^ gaining its first
                # entry is the regression this guard exists for — decided
                # against HEAD^'s tree, so committing the debt and its
                # entry together does not dodge it.
                if _path_exists_in_revision(git, "HEAD^", path):
                    errors.append(
                        f"{path}: baseline entry appeared for an already-tracked "
                        "service file; new bypasses must be reviewed as a "
                        "docs/architecture change (BOUNDARY-DATA-001), not a "
                        "silent baseline edit"
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
