"""Tower test-database naming and collision guards (#1409 P2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers.worktree_collision import _Repo
from tests.helpers.worktree_collision import repo as repo

pytestmark = pytest.mark.no_db


def _add_tower(repo: _Repo, parent_name: str, child_name: str = "wt-101") -> tuple[Path, Path]:
    parent = repo.main / ".worktrees" / parent_name
    if not parent.is_dir():
        parent = repo.add(parent_name)
    child = parent / ".tower" / "worktrees" / child_name
    repo._n += 1
    repo._git(
        "-C",
        str(repo.main),
        "worktree",
        "add",
        "-q",
        "-b",
        f"b{repo._n}",
        str(child),
    )
    return parent, child


def _seed_parent(parent: Path) -> None:
    name = parent.name
    (parent / ".env").write_text(
        f"AGENT_LEGION_DATABASE_URL=postgresql://127.0.0.1:5432/agent_legion_{name}\n"
        f"AGENT_LEGION_S3_BUCKET=agent-legion-{name}\n",
        encoding="utf-8",
    )
    key = parent / "deploy" / "secrets" / "vault_master_key"
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text("parent-key\n", encoding="utf-8")


def test_tower_init_rejects_only_test_database_collision(repo: _Repo) -> None:
    repo.add("develop_wt_101")
    parent, child = _add_tower(repo, "develop")
    _seed_parent(parent)
    repo.install_scripts(child)

    result = repo.run(child / "scripts" / "init-worktree.sh")

    assert result.returncode == 1
    assert "develop_wt_101 -> agent_legion_test_develop_wt_101\n" in result.stderr
    assert "agent_legion_wt_101\n" not in result.stderr
    assert "agent-legion-wt-101\n" not in result.stderr
    assert not (child / ".env").exists()
    assert repo.log.read_text(encoding="utf-8") == ""


def test_tower_init_allows_normal_name_and_cross_parent_twins(repo: _Repo) -> None:
    repo.add("wt_101")
    _add_tower(repo, "other")
    parent, child = _add_tower(repo, "develop")
    _seed_parent(parent)
    repo.install_scripts(child)

    result = repo.run(child / "scripts" / "init-worktree.sh")

    assert result.returncode == 0, result.stderr
    assert (child / ".env").read_text() == (parent / ".env").read_text()
    assert not (child / ".worktree-initialized.ok").exists()
    assert repo.log.read_text(encoding="utf-8") == ""


def test_clean_and_drop_reject_tower_test_database_collision(repo: _Repo) -> None:
    runner = repo.add("runner")
    repo.install_scripts(runner)
    _add_tower(repo, "develop")
    victim = repo.add("develop_wt_101")

    clean = repo.run(runner / "scripts" / "clean-worktree.sh", "develop_wt_101", "--yes")
    drop = repo.run(runner / "scripts" / "drop-worktree-db.sh", "develop_wt_101", "--yes")

    for result in (clean, drop):
        assert result.returncode == 1
        assert "wt-101 -> agent_legion_test_develop_wt_101\n" in result.stderr
    assert victim.is_dir()
    assert repo.log.read_text(encoding="utf-8") == ""


def test_clean_normal_short_name_does_not_hit_distinct_tower_test_db(repo: _Repo) -> None:
    runner = repo.add("runner")
    repo.install_scripts(runner)
    _add_tower(repo, "develop")
    victim = repo.add("wt_101")

    result = repo.run(runner / "scripts" / "clean-worktree.sh", "wt_101", "--yes")

    assert result.returncode == 0, result.stderr
    assert not victim.exists()
    assert "派生出同名资源" not in result.stderr


def test_drop_outside_repo_still_uses_script_repo_for_tower_guard(repo: _Repo) -> None:
    runner = repo.add("runner")
    repo.install_scripts(runner)
    _add_tower(repo, "develop")
    outside = repo.tmp / "outside"
    outside.mkdir()

    result = repo.run(
        runner / "scripts" / "drop-worktree-db.sh",
        "develop_wt_101",
        "--yes",
        cwd=outside,
    )

    assert result.returncode == 1
    assert "wt-101 -> agent_legion_test_develop_wt_101\n" in result.stderr
    assert "not a git repository" not in result.stderr
    assert repo.log.read_text(encoding="utf-8") == ""
