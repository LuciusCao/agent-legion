"""Contract tests for scripts/check_versions.py.

规则 1（清单 ↔ lock 一致）用纯文件 fixture；规则 2（解耦纪律）用真实
mini git repo（git log/show/diff 走真实现，stub 无法覆盖锚点区间语义）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.check_versions import check_all, normalize

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------- fixtures


def _write_repo_files(
    root: Path,
    *,
    repo_version: str = "0.1.0",
    velites_version: str = "0.1.0",
    frontend_version: str = "0.1.0",
    uv_lock_version: str | None = None,
    cargo_lock_version: str | None = None,
    package_lock_version: str | None = None,
) -> None:
    """写入三组件的版本清单与 lock（版本可分别指定，None 表示跟随清单）。"""
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "agent-legion"\nversion = "{repo_version}"\n', encoding="utf-8"
    )
    (root / "uv.lock").write_text(
        f'[[package]]\nname = "agent-legion"\nversion = "{uv_lock_version or repo_version}"\n',
        encoding="utf-8",
    )
    velites = root / "velites"
    velites.mkdir(exist_ok=True)
    (velites / "Cargo.toml").write_text(
        f'[package]\nname = "velites"\nversion = "{velites_version}"\n', encoding="utf-8"
    )
    (velites / "Cargo.lock").write_text(
        f'[[package]]\nname = "velites"\nversion = "{cargo_lock_version or velites_version}"\n',
        encoding="utf-8",
    )
    frontend = root / "frontend"
    frontend.mkdir(exist_ok=True)
    lock_v = package_lock_version or frontend_version
    (frontend / "package.json").write_text(
        '{\n  "name": "agent-legion-frontend",\n'
        f'  "version": "{frontend_version}",\n  "private": true\n}}\n',
        encoding="utf-8",
    )
    (frontend / "package-lock.json").write_text(
        "{\n"
        '  "name": "agent-legion-frontend",\n'
        f'  "version": "{lock_v}",\n'
        '  "packages": {\n'
        '    "": {\n'
        '      "name": "agent-legion-frontend",\n'
        f'      "version": "{lock_v}"\n'
        "    }\n  }\n}\n",
        encoding="utf-8",
    )


def _git(root: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def _init_git_repo(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "test")


def _commit(root: Path, message: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)


# ------------------------------------------------------------ normalize()


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("0.4.0a0", "0.4.0-alpha"),
        ("v0.4.0-Alpha", "0.4.0a0"),
        ("0.4.0-alpha.1", "0.4.0a1"),
        ("0.4.0-alpha", "0.4.0a"),
        ("0.1.0", "0.1.0"),
        ("0.4.0+build.7", "0.4.0"),
        ("0.4.0-beta.2", "0.4.0b2"),
        ("0.4.0-rc", "0.4.0rc0"),
    ],
)
def test_normalize_cross_format_equivalence(left: str, right: str) -> None:
    assert normalize(left) == normalize(right)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("0.4.0a0", "0.4.0"),
        ("0.4.0-alpha", "0.4.1-alpha"),
        ("0.4.0-alpha.1", "0.4.0-alpha.2"),
    ],
)
def test_normalize_distinguishes_real_differences(left: str, right: str) -> None:
    assert normalize(left) != normalize(right)


# -------------------------------------------------------- rule 1: locks


def test_consistent_manifests_and_locks_pass(tmp_path: Path) -> None:
    _write_repo_files(tmp_path)
    errors, notes = check_all(tmp_path)
    assert errors == []
    assert any("版本清单" in note for note in notes)
    # 无 git：解耦规则跳过而不是报错
    assert any("跳过" in note for note in notes)


def test_stale_uv_lock_is_rejected(tmp_path: Path) -> None:
    _write_repo_files(tmp_path, repo_version="0.2.0", uv_lock_version="0.1.0")
    errors, _ = check_all(tmp_path)
    assert any("uv.lock" in error and "uv lock" in error for error in errors)


def test_stale_cargo_lock_is_rejected(tmp_path: Path) -> None:
    _write_repo_files(tmp_path, velites_version="0.2.0", cargo_lock_version="0.1.0")
    errors, _ = check_all(tmp_path)
    assert any("Cargo.lock" in error and "cargo update -w" in error for error in errors)


def test_stale_package_lock_is_rejected(tmp_path: Path) -> None:
    _write_repo_files(tmp_path, frontend_version="0.2.0", package_lock_version="0.1.0")
    errors, _ = check_all(tmp_path)
    # package-lock 两处版本字段都必须跟清单一致
    assert len([error for error in errors if "package-lock" in error]) == 2


def test_missing_manifest_is_rejected(tmp_path: Path) -> None:
    _write_repo_files(tmp_path)
    (tmp_path / "velites/Cargo.toml").unlink()
    errors, _ = check_all(tmp_path)
    assert any("velites" in error and "找不到" in error for error in errors)


# --------------------------------------------------- rule 2: decoupling


def test_release_bump_after_source_changes_passes(tmp_path: Path) -> None:
    """特性先合入、版本后落版：正常节奏，锚点区间内有源码改动。"""
    root = tmp_path / "repo"
    root.mkdir()
    _init_git_repo(root)
    _write_repo_files(root)
    _commit(root, "baseline 0.1.0")
    (root / "velites/src/lib.rs").parent.mkdir(parents=True, exist_ok=True)
    (root / "velites/src/lib.rs").write_text("pub fn new() {}\n", encoding="utf-8")
    _commit(root, "feat(velites): new feature")
    _write_repo_files(root, velites_version="0.2.0")
    _commit(root, "chore(release): velites 0.2.0")

    errors, notes = check_all(root)
    assert errors == []
    assert any("velites" in note and "✓" in note for note in notes)


def test_lockstep_bump_without_source_changes_is_rejected(tmp_path: Path) -> None:
    """仓库发版顺手 bump velites：锚点区间内无源码改动，拒绝。"""
    root = tmp_path / "repo"
    root.mkdir()
    _init_git_repo(root)
    _write_repo_files(root)
    _commit(root, "baseline 0.1.0")
    _write_repo_files(root, velites_version="0.2.0")
    _commit(root, "chore(release): 仓库 0.2.0（锁步 bump velites）")

    errors, _ = check_all(root)
    assert any("锁步" in error and "velites/Cargo.toml" in error for error in errors)


def test_anchor_is_last_version_change_not_head_caret(tmp_path: Path) -> None:
    """锚点语义的区分性用例（评审 P2）。

    序列：baseline → velites 特性合入 → 仓库落版（只动 pyproject）→
    velites 落版。velites 版本的上一次变化在 baseline，锚点应取 baseline
    （区间含 feat 提交 → 放行）；朴素的 HEAD^ 语义会把锚点定在「仓库落版」
    上（区间只有 pyproject 变化 → 误拒合法的组件落版）。此用例钉住
    「锚点 = 版本上一次 differing 的提交」这一核心设计决策，防止未来被
    "简化"成 HEAD^ 而测试全绿。
    """
    root = tmp_path / "repo"
    root.mkdir()
    _init_git_repo(root)
    _write_repo_files(root)
    _commit(root, "baseline 0.1.0")
    (root / "velites/src/lib.rs").parent.mkdir(parents=True, exist_ok=True)
    (root / "velites/src/lib.rs").write_text("pub fn new() {}\n", encoding="utf-8")
    _commit(root, "feat(velites): new feature")
    _write_repo_files(root, repo_version="0.2.0")
    _commit(root, "chore(release): 仓库 0.2.0（不含 velites）")
    _write_repo_files(root, velites_version="0.2.0")
    _commit(root, "chore(release): velites 0.2.0")

    errors, notes = check_all(root)
    assert errors == []
    assert any("velites" in note and "✓" in note for note in notes)


def test_uncommitted_lockstep_bump_is_rejected(tmp_path: Path) -> None:
    """工作区里未提交的锁步 bump 同样拦得住（diff 到工作树）。"""
    root = tmp_path / "repo"
    root.mkdir()
    _init_git_repo(root)
    _write_repo_files(root)
    _commit(root, "baseline 0.1.0")
    _write_repo_files(root, frontend_version="0.2.0")

    errors, _ = check_all(root)
    assert any("frontend/package.json" in error for error in errors)


def test_unchanged_version_has_no_decoupling_rule(tmp_path: Path) -> None:
    """版本自首次出现从未变过：无锁步可言，不触发规则 2。"""
    root = tmp_path / "repo"
    root.mkdir()
    _init_git_repo(root)
    _write_repo_files(root)
    _commit(root, "baseline 0.1.0")
    (root / "velites/src/lib.rs").parent.mkdir(parents=True, exist_ok=True)
    (root / "velites/src/lib.rs").write_text("pub fn new() {}\n", encoding="utf-8")
    _commit(root, "feat(velites): new feature, version stays")

    errors, notes = check_all(root)
    assert errors == []
    assert not any("✓" in note for note in notes)


def test_pyproject_repo_version_is_exempt_from_decoupling(tmp_path: Path) -> None:
    """仓库整体版本的落版提交只有清单/lock 变化是正常的，不适用规则 2。"""
    root = tmp_path / "repo"
    root.mkdir()
    _init_git_repo(root)
    _write_repo_files(root)
    _commit(root, "baseline 0.1.0")
    _write_repo_files(root, repo_version="0.2.0")
    _commit(root, "chore(release): 仓库 0.2.0")

    errors, _ = check_all(root)
    assert errors == []
