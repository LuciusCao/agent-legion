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

import json
import os
from pathlib import Path

from .budget_git import BudgetGitUnavailable, GitHelper

__test__ = False

BOUNDARY_BASELINE_RELATIVE_PATH = "config/architecture/service-data-boundary-baseline.json"

_SHALLOW_OPT_OUT = "AGENT_LEGION_BUDGET_MONOTONICITY_SHALLOW"
_ANCHORS = ("HEAD", "HEAD^")


def committed_boundary_entries(text: str | None) -> dict[str, tuple[int, int, int]]:
    """Path -> (sql, primitives, dsn) from a committed baseline JSON (lenient)."""
    if text is None:
        return {}
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return {}
    files = raw.get("files") if isinstance(raw, dict) else None
    if not isinstance(files, dict):
        return {}
    entries: dict[str, tuple[int, int, int]] = {}
    for key, value in files.items():
        if not isinstance(key, str):
            continue
        if (
            isinstance(value, list)
            and len(value) == 3
            and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
        ):
            entries[key] = (value[0], value[1], value[2])
    return entries


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
    Renames are left to the plain check: the scan keys real paths on disk,
    so a renamed service file with bypasses surfaces as a fresh
    no-baseline-entry error, which is the honest signal.
    """
    git = GitHelper(root)
    try:
        errors = _unresolvable_anchor_errors(git)
        if errors:
            return errors
        floors: dict[str, tuple[int, int, int]] = {}
        seen_paths: set[str] = set()
        for revision in _ANCHORS:
            committed = committed_boundary_entries(
                git.committed_file_text(revision, BOUNDARY_BASELINE_RELATIVE_PATH)
            )
            seen_paths.update(committed)
            for path, triple in committed.items():
                if path not in floors or any(
                    a < b for a, b in zip(triple, floors[path], strict=True)
                ):
                    floors[path] = triple

        for path, triple in baseline_files.items():
            if path not in seen_paths:
                # First-time entry for a path the anchors never tracked. A
                # brand-new service file legitimately starts here; an
                # existing tracked service file gaining its first bypasses
                # is the regression this guard exists for. Distinguish by
                # whether the path exists in any anchor revision at all —
                # checked against HEAD's tree only (cheap and sufficient:
                # the file existing yesterday but entering the baseline
                # today means debt was added, not a new file registered).
                if _path_exists_in_revision(git, "HEAD", path):
                    errors.append(
                        f"{path}: baseline entry appeared for an already-tracked "
                        "service file; new bypasses need the exemption channel "
                        "(docs/architecture review), not a baseline edit"
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
