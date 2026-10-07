"""Derived-name collision guards for init/clean/drop worktree scripts (#950).

Worktree name -> DB name (non ``[a-zA-Z0-9_]`` folded to ``_``) and bucket
name (lowercased, non ``[a-z0-9-]`` folded to ``-``) is not injective, so two
worktrees can silently share one database/bucket and cleaning one drops the
other's. The scripts now refuse when another still-present worktree derives
the same name.

Tests build a real temporary git repo with real ``git worktree add``
worktrees (so ``git worktree list --porcelain`` parsing is exercised for
real), copy the scripts into a worktree, and stub ``psql``/``uv`` on a
restricted PATH: no real database or bucket is touched.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = (
    "init-worktree.sh",
    "clean-worktree.sh",
    "drop-worktree-db.sh",
    "worktree-names-lib.sh",
    "ensure-s3-bucket.py",
)

_GIT = shutil.which("git")

# psql 桩：记录每次调用；所有探测都查不到库（安全 no-op 路径）。
_PSQL_STUB = """#!/usr/bin/env bash
printf 'psql %s\\n' "$*" >>"$STUB_LOG"
exit 0
"""

# uv 桩：init 的 vault key 生成拿到一行输出；clean 的 S3 heredoc 走
# 「endpoint 不可达」降级分支。
_UV_STUB = """#!/usr/bin/env bash
printf 'uv %s\\n' "$*" >>"$STUB_LOG"
echo "stub-vault-master-key"
"""


def _exe(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _git_env(tmp_path: Path) -> dict[str, str]:
    return {
        "HOME": str(tmp_path / "home"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }


class _Repo:
    def __init__(self, tmp_path: Path) -> None:
        assert _GIT is not None
        self.tmp = tmp_path.resolve()
        (self.tmp / "home").mkdir()
        self.main = self.tmp / "main"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.log = self.tmp / "stub.log"
        self.log.write_text("", encoding="utf-8")
        # 只暴露 git 本身（包装脚本），不把 git 所在目录整个放上 PATH——
        # 那里可能同时有真 createdb/psql。
        _exe(self.bin / "git", f'#!/usr/bin/env bash\nexec "{_GIT}" "$@"\n')
        _exe(self.bin / "psql", _PSQL_STUB)
        _exe(self.bin / "uv", _UV_STUB)
        self._git("init", "-q", str(self.main))
        self._git("-C", str(self.main), "commit", "-q", "--allow-empty", "-m", "init")
        self._n = 0

    def _git(self, *args: str) -> None:
        env = {**_git_env(self.tmp), "PATH": "/usr/bin:/bin"}
        subprocess.run([_GIT, *args], check=True, capture_output=True, env=env)

    def add(self, name: str) -> Path:
        self._n += 1
        path = self.main / ".worktrees" / name
        self._git("-C", str(self.main), "worktree", "add", "-q", "-b", f"b{self._n}", str(path))
        return path

    def install_scripts(self, worktree: Path) -> None:
        scripts = worktree / "scripts"
        scripts.mkdir(exist_ok=True)
        for name in _SCRIPTS:
            shutil.copy(ROOT / "scripts" / name, scripts / name)

    def run(
        self, script: Path, *args: str, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = {
            **_git_env(self.tmp),
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "STUB_LOG": str(self.log),
        }
        return subprocess.run(
            ["bash", str(script), *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=cwd or self.main,
            timeout=60,
        )


@pytest.fixture
def repo(tmp_path: Path) -> _Repo:
    if _GIT is None:
        pytest.skip("git not available")
    return _Repo(tmp_path)


# (新 worktree 名, 已存在的 worktree 名, 撞上的派生资源)
_COLLIDING = [
    (
        "foo-bar",
        "foo_bar",
        ["agent_legion_foo_bar", "agent_legion_test_foo_bar", "agent-legion-foo-bar"],
    ),
    (
        "release-0-7-8",
        "release-0.7.8",
        [
            "agent_legion_release_0_7_8",
            "agent_legion_test_release_0_7_8",
            "agent-legion-release-0-7-8",
        ],
    ),
    # 只差大小写：开发库保留大小写不撞，测试库与 bucket 小写化后撞。
    ("feat-a", "Feat_A", ["agent_legion_test_feat_a", "agent-legion-feat-a"]),
]


# --- init-worktree.sh ---------------------------------------------------------


@pytest.mark.parametrize(("new", "existing", "resources"), _COLLIDING)
def test_init_rejects_derived_name_collision(
    repo: _Repo, new: str, existing: str, resources: list[str]
) -> None:
    repo.add(existing)
    target = repo.add(new)
    repo.install_scripts(target)
    (target / ".env").write_text("# stub env\n")

    result = repo.run(target / "scripts" / "init-worktree.sh")

    assert result.returncode == 1, result.stdout
    assert "派生出同名资源" in result.stderr
    for resource in resources:
        assert f"{existing} -> {resource}\n" in result.stderr
    # 只列真正撞上的资源（大小写场景下开发库不算冲突）。
    assert result.stderr.count(f"{existing} -> ") == len(resources)
    # fail-fast 在任何副作用之前：.env 未被改写，未调 uv / psql。
    assert (target / ".env").read_text() == "# stub env\n"
    assert repo.log.read_text(encoding="utf-8") == ""
    assert not (target / "deploy").exists()


def test_init_without_collision_initializes(repo: _Repo) -> None:
    repo.add("foo_bar")
    target = repo.add("foo-baz")
    repo.install_scripts(target)
    (target / ".env").write_text("# stub env\n")

    result = repo.run(target / "scripts" / "init-worktree.sh")

    assert result.returncode == 0, result.stderr
    env = (target / ".env").read_text()
    assert "AGENT_LEGION_DATABASE_URL=postgresql://127.0.0.1:5432/agent_legion_foo_baz" in env
    assert "AGENT_LEGION_S3_BUCKET=agent-legion-foo-baz" in env


def test_init_ignores_prunable_worktree_whose_directory_is_gone(repo: _Repo) -> None:
    gone = repo.add("foo_bar")
    shutil.rmtree(gone)  # 仍在 worktree list 里（prunable），但已无使用方
    target = repo.add("foo-bar")
    repo.install_scripts(target)
    (target / ".env").write_text("# stub env\n")

    result = repo.run(target / "scripts" / "init-worktree.sh")

    assert result.returncode == 0, result.stderr
    assert "派生出同名资源" not in result.stderr


# --- clean-worktree.sh / drop-worktree-db.sh -------------------------------


@pytest.mark.parametrize(("victim", "survivor", "resources"), _COLLIDING)
def test_clean_refuses_when_another_worktree_shares_derived_names(
    repo: _Repo, victim: str, survivor: str, resources: list[str]
) -> None:
    runner = repo.add("runner")
    repo.install_scripts(runner)
    repo.add(survivor)
    victim_path = repo.add(victim)

    result = repo.run(runner / "scripts" / "clean-worktree.sh", victim, "--yes")

    assert result.returncode == 1, result.stdout
    assert "拒绝清理" in result.stderr
    for resource in resources:
        assert f"{survivor} -> {resource}\n" in result.stderr
    # 在第 1 步之前拒绝：worktree 仍在，未碰库与 bucket。
    assert victim_path.is_dir()
    assert repo.log.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize(("victim", "survivor", "resources"), _COLLIDING)
def test_drop_db_refuses_when_another_worktree_shares_derived_names(
    repo: _Repo, victim: str, survivor: str, resources: list[str]
) -> None:
    runner = repo.add("runner")
    repo.install_scripts(runner)
    repo.add(survivor)

    # victim 已被移除（独立调用 drop 的典型时机），survivor 仍在用同名库。
    result = repo.run(runner / "scripts" / "drop-worktree-db.sh", victim, "--yes")

    assert result.returncode == 1, result.stdout
    assert "拒绝删除派生库" in result.stderr
    assert f"{survivor} -> {resources[0]}\n" in result.stderr
    assert repo.log.read_text(encoding="utf-8") == ""


def test_clean_without_collision_proceeds(repo: _Repo) -> None:
    runner = repo.add("runner")
    repo.install_scripts(runner)
    repo.add("foo_bar")
    victim_path = repo.add("foo-baz")

    result = repo.run(runner / "scripts" / "clean-worktree.sh", "foo-baz", "--yes")

    assert result.returncode == 0, result.stderr
    assert not victim_path.exists()
    assert "不存在（跳过）: agent_legion_foo_baz\n" in result.stdout
    assert "不存在（跳过）: agent_legion_test_foo_baz\n" in result.stdout
    assert "收尾清理结束" in result.stdout
    assert "派生出同名资源" not in result.stderr


def test_drop_db_without_collision_proceeds(repo: _Repo) -> None:
    runner = repo.add("runner")
    repo.install_scripts(runner)
    repo.add("foo_bar")

    result = repo.run(runner / "scripts" / "drop-worktree-db.sh", "release-0.7.8", "--yes")

    assert result.returncode == 0, result.stderr
    assert "没有需要删除的库" in result.stdout
