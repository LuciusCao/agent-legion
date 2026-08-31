"""Tests for the AGENT_LEGION_BUDGET_BASE anchor override.

The override replaces the HEAD^ anchor with an explicit PR base (e.g.
``origin/develop``) so a local run reproduces CI's merge-ref judgement: on
the merge ref HEAD^ IS the PR base, while a local HEAD^ is only the
branch's own previous commit and cannot see a raise committed earlier in
the branch. Shared git fixtures come from ``tests/architecture_budget_helpers``
(test modules must not import each other).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.architecture.budget_policy import BudgetPolicy
from scripts.architecture.file_budgets import check_file_budgets
from scripts.architecture.service_data_boundary import check_service_data_boundary
from tests.architecture_budget_helpers import (
    boundary_git_repo,
    commit_all,
    git_repo,
    write_baseline,
    write_boundary_baseline,
)

pytestmark = pytest.mark.no_db

_BASE_ENV = "AGENT_LEGION_BUDGET_BASE"
_RELEASE_TRAIN_ENV = "AGENT_LEGION_BUDGET_MONOTONICITY_RELEASE_TRAIN"
_SHALLOW_ENV = "AGENT_LEGION_BUDGET_MONOTONICITY_SHALLOW"


def _repo_with_buried_raise(tmp_path: Path) -> tuple[Path, BudgetPolicy]:
    """Repo whose ceiling raise is invisible to the default HEAD^ anchor.

    The raise (105 → 110) is committed, then buried under an unrelated
    follow-up commit, so HEAD and HEAD^ agree on the raised ceiling and the
    default anchors see no raise. The ``pr-base`` branch points at the
    pre-raise commit — the shape a CI merge ref has (its HEAD^ is the PR
    base), which a local HEAD^ cannot reproduce. Both ceilings stay within
    actual + buffer (100 lines, buffer 10) so only the monotonic rule can
    complain.
    """
    root, policy = git_repo(tmp_path, files={"server/app/example.py": 105})
    subprocess.run(["git", "branch", "pr-base"], cwd=root, check=True)
    write_baseline(root, {"server/app/example.py": 110})
    commit_all(root, "raise ceiling by hand")
    (root / "server" / "app" / "other.py").write_text("\n".join(["line"] * 5), encoding="utf-8")
    write_baseline(root, {"server/app/example.py": 110, "server/app/other.py": 15})
    commit_all(root, "unrelated follow-up")
    return root, policy


def test_base_anchor_override_reproduces_ci_merge_ref_judgement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, policy = _repo_with_buried_raise(tmp_path)
    monkeypatch.delenv(_BASE_ENV, raising=False)
    monkeypatch.delenv(_RELEASE_TRAIN_ENV, raising=False)
    # Default anchors: HEAD and HEAD^ both carry the raised ceiling, so the
    # buried raise passes locally — exactly the gap the override closes.
    assert check_file_budgets(root, policy, ()) == []
    monkeypatch.setenv(_BASE_ENV, "pr-base")
    errors = check_file_budgets(root, policy, ())
    assert any(
        "ceiling 110 rose above committed ceiling 105" in error and "pr-base" in error
        for error in errors
    )


def test_base_anchor_unresolvable_hard_fails_with_fetch_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An explicitly configured base ref must resolve: silently skipping it
    # would gut the very comparison the user asked for. The shallow opt-out
    # covers missing history, not a bad ref name, so it must not rescue the
    # misconfigured base either.
    root, policy = git_repo(tmp_path)
    monkeypatch.setenv(_BASE_ENV, "origin/does-not-exist")
    monkeypatch.setenv(_SHALLOW_ENV, "1")
    errors = check_file_budgets(root, policy, ())
    assert any(
        "origin/does-not-exist" in error and "does not resolve" in error and "git fetch" in error
        for error in errors
    )


def test_release_train_opt_out_precedes_base_anchor_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Release train semantics are "ignore the base's lagging floor", so the
    # opt-out wins over a simultaneously configured base override and the
    # anchors collapse to HEAD only.
    root, policy = _repo_with_buried_raise(tmp_path)
    monkeypatch.setenv(_BASE_ENV, "pr-base")
    monkeypatch.setenv(_RELEASE_TRAIN_ENV, "1")
    assert check_file_budgets(root, policy, ()) == []


def test_boundary_guard_honors_base_anchor_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The boundary guard shares the anchor plumbing: a raised triple buried
    # under a trailing commit passes HEAD / HEAD^ but fails against the base.
    root = boundary_git_repo(tmp_path, {"server/app/services/legacy.py": [3, 1, 0]})
    subprocess.run(["git", "branch", "pr-base"], cwd=root, check=True)
    write_boundary_baseline(root, {"server/app/services/legacy.py": [4, 1, 0]})
    commit_all(root, "raise boundary count by hand")
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "unrelated follow-up"],
        cwd=root,
        check=True,
    )
    monkeypatch.delenv(_BASE_ENV, raising=False)
    assert not any(
        "rose above committed floor" in error for error in check_service_data_boundary(root)
    )
    monkeypatch.setenv(_BASE_ENV, "pr-base")
    errors = check_service_data_boundary(root)
    assert any(
        "baseline triple [4, 1, 0] rose above committed floor [3, 1, 0]" in error
        for error in errors
    )
