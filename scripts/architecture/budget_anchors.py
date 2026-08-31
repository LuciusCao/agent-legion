"""Shared anchor plumbing for the monotonic (only-down) guards.

Both monotonicity guards (budget ceilings #209, service data-boundary
baseline #292) compare the working tree against committed anchors —
``HEAD`` / ``HEAD^`` by default. ``AGENT_LEGION_BUDGET_BASE`` (e.g.
``origin/develop``) replaces ``HEAD^`` with an explicit PR base so a local
run reproduces CI's merge-ref judgement exactly: on the merge ref HEAD^ IS
the PR base, while a local HEAD^ is only the branch's own previous commit
and cannot see a raise committed earlier in the branch. The floor stays
the minimum effective ceiling across ``HEAD`` and the base ref.

The release-train opt-out
(``AGENT_LEGION_BUDGET_MONOTONICITY_RELEASE_TRAIN=1``) takes precedence
over the base override: its whole point is ignoring the base's lagging
floor, so resolving a base anchor there would contradict it.

Anchor error wording and the per-anchor floor merge live here (not in the
guards) because the guards are file-size budgeted themselves and had no
headroom left for the feature.
"""

from __future__ import annotations

import os

from .budget_git import GitHelper
from .budget_registry_history import (
    BUDGETS_RELATIVE_PATH,
    EXEMPTIONS_RELATIVE_PATH,
    committed_budget_ceilings,
    committed_exemption_ceilings,
)

__test__ = False

BASE_ANCHOR_OVERRIDE_ENV = "AGENT_LEGION_BUDGET_BASE"
SHALLOW_OPT_OUT_ENV = "AGENT_LEGION_BUDGET_MONOTONICITY_SHALLOW"
RELEASE_TRAIN_OPT_OUT_ENV = "AGENT_LEGION_BUDGET_MONOTONICITY_RELEASE_TRAIN"
DEFAULT_ANCHORS = ("HEAD", "HEAD^")


def base_anchor_override() -> str | None:
    """Explicit PR-base ref from the env override; None when unset/blank."""
    value = os.environ.get(BASE_ANCHOR_OVERRIDE_ENV, "").strip()
    return value or None


def anchor_revisions(*, release_train: bool) -> tuple[str, ...]:
    """Anchor revisions: release train → HEAD only; override → HEAD + base."""
    if release_train:
        return ("HEAD",)
    base = base_anchor_override()
    return ("HEAD", base) if base else DEFAULT_ANCHORS


def shallow_opt_out() -> bool:
    """Explicit opt-out for depth-1 checkouts that cannot fetch history."""
    return os.environ.get(SHALLOW_OPT_OUT_ENV) == "1"


def release_train_opt_out() -> bool:
    """Release-train opt-out shared by both guards (precedence: see docstring)."""
    return os.environ.get(RELEASE_TRAIN_OPT_OUT_ENV) == "1"


def unresolvable_anchor_error(check: str, revision: str, details: str) -> str:
    """Shallow-clone / git-error anchor failure with the opt-out pointer."""
    return (
        f"{check} monotonicity: git anchor {revision} does not resolve in this "
        "checkout (shallow clone / git error?); fetch history (CI: "
        f"fetch-depth: 0) or set {SHALLOW_OPT_OUT_ENV}=1 to skip the check"
        + (f"; git failure: {details}" if details else "")
    )


def unresolvable_base_anchor_error(check: str, revision: str) -> str:
    """An explicitly configured base ref must resolve — always a hard error."""
    return (
        f"{check} monotonicity: base anchor {revision} from "
        f"{BASE_ANCHOR_OVERRIDE_ENV} does not resolve in this checkout; fetch it "
        "(e.g. git fetch origin develop) or fix the ref name, or unset "
        f"{BASE_ANCHOR_OVERRIDE_ENV}"
    )


def base_floor_anchor(source: str | None) -> str | None:
    """``source`` when it is the configured base override, else None."""
    return source if source is not None and source == base_anchor_override() else None


def anchor_budget_floors(
    git: GitHelper, anchors: tuple[str, ...]
) -> tuple[dict[str, int], dict[str, int], dict[str, str], dict[str, str]]:
    """Effective-ceiling floors per path, minimum across committed anchors.

    Each anchor contributes max(baseline entry, exemption ceiling) per path —
    the effective ceiling a raise must not exceed. Taking the min across
    anchors catches a raise introduced at any layer (uncommitted edit vs
    HEAD, smuggled-into-pending-commit vs HEAD^ or the base override), while
    a working-tree revert of a committed raise passes. The source maps
    record which anchor supplied each floor so the error can name a base
    override as the floor's origin.
    """
    budget_floors: dict[str, int] = {}
    exemption_floors: dict[str, int] = {}
    budget_sources: dict[str, str] = {}
    exemption_sources: dict[str, str] = {}
    for revision in anchors:
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
                budget_sources[path] = revision
        for path, committed in previous_exemptions.items():
            if path not in exemption_floors or committed < exemption_floors[path]:
                exemption_floors[path] = committed
                exemption_sources[path] = revision
    return budget_floors, exemption_floors, budget_sources, exemption_sources
