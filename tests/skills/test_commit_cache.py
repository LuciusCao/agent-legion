"""Shared (skill, commit) materialization cache (issue #569).

Pins the cache contract: a hit is a pure path probe (zero git calls), a
markerless half-exported directory is never a hit, the per-skill cache is
LRU-bounded at KEPT_COMMITS_PER_SKILL, and neither per-validation cleanup
nor the stale-execution sweeper touches the shared subtree.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from server.app.skills.commit_cache import (
    COMPLETE_MARKER,
    KEPT_COMMITS_PER_SKILL,
    materialized_commit_dir,
    resolve_skill_commit,
    shared_cache_root,
)
from server.app.skills.errors import SkillRepoError
from server.app.skills.runs_gc import SHARED_DIR_NAME, sweep_stale_execution_dirs
from tests.helpers.skill_git import (
    _KEY,
    _commit_skill_update,
    _head_commit,
    _make_manager,
    _make_skill_repo,
    _tag,
)

pytestmark = pytest.mark.no_db


def _git_call_count(manager, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []
    real_run_git = manager._run_git

    def _spy(args: list[str], check: bool = True):
        calls.append(list(args))
        return real_run_git(args, check=check)

    monkeypatch.setattr(manager, "_run_git", _spy)
    return calls


def test_second_materialization_of_same_commit_is_a_pure_path_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _make_skill_repo(tmp_path / "skills", _KEY)
    commit = _head_commit(repo)
    manager = _make_manager(tmp_path)

    first = materialized_commit_dir(manager, _KEY, commit)
    assert (first / "SKILL.md").is_file()

    calls = _git_call_count(manager, monkeypatch)
    second = materialized_commit_dir(manager, _KEY, commit)

    assert second == first
    assert calls == [], f"cache hit must not spawn git: {calls}"


def test_markerless_half_exported_dir_is_never_a_hit(tmp_path: Path) -> None:
    repo = _make_skill_repo(tmp_path / "skills", _KEY)
    commit = _head_commit(repo)
    manager = _make_manager(tmp_path)

    run_dir = materialized_commit_dir(manager, _KEY, commit)
    commit_dir = run_dir.parents[1]
    # Simulate a crash between the atomic rename and the marker write.
    (commit_dir / COMPLETE_MARKER).unlink()
    (run_dir / "SKILL.md").write_text("corrupted half-export\n")

    rerun = materialized_commit_dir(manager, _KEY, commit)

    assert rerun == run_dir
    assert (rerun / "SKILL.md").read_text() == "# skill\n"
    assert (commit_dir / COMPLETE_MARKER).is_file()


def test_cache_is_lru_bounded_per_skill(tmp_path: Path) -> None:
    repo = _make_skill_repo(tmp_path / "skills", _KEY)
    manager = _make_manager(tmp_path)
    commits = [_head_commit(repo)]
    for index in range(KEPT_COMMITS_PER_SKILL):  # one more than the cap
        commits.append(_commit_skill_update(repo, f"# v{index}\n"))

    # Explicit increasing mtimes: eviction order must not hinge on
    # filesystem mtime granularity.
    for index, commit in enumerate(commits[:-1]):
        run_dir = materialized_commit_dir(manager, _KEY, commit)
        os.utime(run_dir.parents[1], (1000 + index, 1000 + index))
    materialized_commit_dir(manager, _KEY, commits[-1])

    skill_root = shared_cache_root(manager.runs_dir) / _KEY
    survivors = {entry.name for entry in skill_root.iterdir() if entry.is_dir()}
    assert len(survivors) == KEPT_COMMITS_PER_SKILL
    assert commits[0] not in survivors  # oldest evicted
    assert commits[-1] in survivors


def test_hit_refreshes_lru_position(tmp_path: Path) -> None:
    repo = _make_skill_repo(tmp_path / "skills", _KEY)
    manager = _make_manager(tmp_path)
    commits = [_head_commit(repo)]
    for index in range(KEPT_COMMITS_PER_SKILL - 1):
        commits.append(_commit_skill_update(repo, f"# v{index}\n"))
    for index, commit in enumerate(commits):
        run_dir = materialized_commit_dir(manager, _KEY, commit)
        os.utime(run_dir.parents[1], (1000 + index, 1000 + index))

    # Touch the oldest entry, then materialize one more commit: the touched
    # entry must survive while the new oldest is evicted.
    materialized_commit_dir(manager, _KEY, commits[0])
    newest = _commit_skill_update(repo, "# newest\n")
    materialized_commit_dir(manager, _KEY, newest)

    skill_root = shared_cache_root(manager.runs_dir) / _KEY
    survivors = {entry.name for entry in skill_root.iterdir() if entry.is_dir()}
    assert commits[0] in survivors
    assert commits[1] not in survivors


def test_dash_dash_components_do_not_collide_buckets(tmp_path: Path) -> None:
    """PR #571 review regression: ``a--b/c`` and ``a/b--c`` must land in
    DISTINCT nested buckets (``--`` is legal inside a key component), so one
    skill's LRU eviction can never rmtree the other's tree."""
    repo1 = _make_skill_repo(tmp_path / "skills", "a--b/c")
    repo2 = _make_skill_repo(tmp_path / "skills", "a/b--c")
    manager = _make_manager(tmp_path)
    commit2 = _head_commit(repo2)

    run_dir1 = materialized_commit_dir(manager, "a--b/c", _head_commit(repo1))
    run_dir2 = materialized_commit_dir(manager, "a/b--c", commit2)
    assert run_dir1 != run_dir2
    assert run_dir1.is_dir() and run_dir2.is_dir()

    # Push skill1 past the eviction cap: skill2's tree must survive.
    for index in range(KEPT_COMMITS_PER_SKILL):
        materialized_commit_dir(manager, "a--b/c", _commit_skill_update(repo1, f"# v{index}\n"))

    assert run_dir2.is_dir()
    assert (run_dir2 / "SKILL.md").is_file()


def test_tmp_export_leftover_is_reclaimed_on_next_materialization(tmp_path: Path) -> None:
    repo = _make_skill_repo(tmp_path / "skills", _KEY)
    manager = _make_manager(tmp_path)
    materialized_commit_dir(manager, _KEY, _head_commit(repo))

    # Simulate a crashed export's temp dir (never renamed into place).
    leftover = shared_cache_root(manager.runs_dir) / _KEY / ".tmp-deadbeef"
    leftover.mkdir(parents=True)
    (leftover / "partial").write_text("x\n")

    materialized_commit_dir(manager, _KEY, _commit_skill_update(repo, "# v2\n"))

    assert not leftover.exists()


def test_cleanup_execution_cannot_reach_the_shared_cache(tmp_path: Path) -> None:
    repo = _make_skill_repo(tmp_path / "skills", _KEY)
    commit = _head_commit(repo)
    manager = _make_manager(tmp_path)
    run_dir = materialized_commit_dir(manager, _KEY, commit)

    # The validation path no longer creates per-validation execution dirs;
    # cleanup of any such id must be a no-op against the shared subtree.
    manager.cleanup_execution("validate-deadbeef")

    assert run_dir.is_dir()
    assert (run_dir.parents[1] / COMPLETE_MARKER).is_file()


def test_sweeper_skips_the_shared_cache(tmp_path: Path) -> None:
    repo = _make_skill_repo(tmp_path / "skills", _KEY)
    commit = _head_commit(repo)
    manager = _make_manager(tmp_path)
    run_dir = materialized_commit_dir(manager, _KEY, commit)
    stale_exec = manager.runs_dir / "stale-exec"
    stale_exec.mkdir(parents=True)

    swept = sweep_stale_execution_dirs(manager.runs_dir, max_age_seconds=0)

    assert swept == 1
    assert not stale_exec.exists()
    assert run_dir.is_dir(), f"sweeper must never enter {SHARED_DIR_NAME}"


def test_resolve_skill_commit_matches_checkout_semantics(tmp_path: Path) -> None:
    repo = _make_skill_repo(tmp_path / "skills", _KEY)
    manager = _make_manager(tmp_path)
    tagged = _tag(repo, "v1")

    assert resolve_skill_commit(manager, _KEY, None) == _head_commit(repo)
    assert resolve_skill_commit(manager, _KEY, "v1") == tagged
    # A pinned ref freezes into the lock document on first resolution.
    assert manager.load_lock().skills[_KEY].refs["v1"] == tagged


def test_malformed_commit_fails_closed(tmp_path: Path) -> None:
    _make_skill_repo(tmp_path / "skills", _KEY)
    manager = _make_manager(tmp_path)

    with pytest.raises(SkillRepoError, match="40-hex"):
        materialized_commit_dir(manager, _KEY, "latest")


def test_commit_missing_from_repo_fails_closed(tmp_path: Path) -> None:
    _make_skill_repo(tmp_path / "skills", _KEY)
    manager = _make_manager(tmp_path)

    with pytest.raises(SkillRepoError, match="missing"):
        materialized_commit_dir(manager, _KEY, "0" * 40)
