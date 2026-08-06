"""Contract tests for scripts/init-worktree.sh nested-worktree guard.

The script resolves ROOT from its own location, so tests copy it into a
synthetic repo layout and run it with stubbed ``git``/``uv`` on a restricted
PATH: no real repo, database, or vault key is touched.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "init-worktree.sh"

_GIT_STUB = """#!/usr/bin/env bash
if [[ "$1" == "worktree" && "$2" == "list" ]]; then
  echo "worktree {main}"
  echo "bare"
  exit 0
fi
echo "unexpected git call: $*" >&2
exit 1
"""

_UV_STUB = """#!/usr/bin/env bash
echo "stub-vault-master-key"
"""


def _write_stub(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _setup(tmp_path: Path, script_rel: str) -> tuple[Path, Path]:
    """Lay out main/.worktrees/... with the script at script_rel; stub bin dir."""
    main = tmp_path / "main"
    script_path = main / script_rel
    script_path.parent.mkdir(parents=True)
    shutil.copy(SCRIPT, script_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(bin_dir / "git", _GIT_STUB.format(main=main))
    _write_stub(bin_dir / "uv", _UV_STUB)
    return main, bin_dir


def _run(script_path: Path, bin_dir: Path) -> subprocess.CompletedProcess[str]:
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": os.environ.get("HOME", "")}
    return subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_nested_worktree_is_rejected(tmp_path: Path) -> None:
    main, bin_dir = _setup(tmp_path, ".worktrees/dev/.worktrees/nested/scripts/init-worktree.sh")
    script_path = main / ".worktrees/dev/.worktrees/nested/scripts/init-worktree.sh"

    result = _run(script_path, bin_dir)

    assert result.returncode == 1
    assert "禁止嵌套" in result.stderr
    assert str(main) in result.stderr
    # Guard fires before any side effect.
    assert not (main / ".worktrees/dev/.worktrees/nested/deploy").exists()


def test_flat_worktree_passes_guard_and_initializes(tmp_path: Path) -> None:
    main, bin_dir = _setup(tmp_path, ".worktrees/flat/scripts/init-worktree.sh")
    # .env 从基准 worktree 复制；合成的 main 需要先有一个。
    (main / ".env").write_text("# stub env\n")
    script_path = main / ".worktrees/flat/scripts/init-worktree.sh"

    result = _run(script_path, bin_dir)

    assert result.returncode == 0, result.stderr
    worktree = main / ".worktrees/flat"
    assert (
        "AGENT_LEGION_DATABASE_URL=postgresql://127.0.0.1:5432/agent_legion_flat"
        in (worktree / ".env").read_text()
    )
    assert (worktree / "deploy/secrets/agent_worker_register_token").is_file()
    assert (worktree / "deploy/secrets/vault_master_key").read_text().strip() == (
        "stub-vault-master-key"
    )


def test_main_repo_root_exits_without_side_effects(tmp_path: Path) -> None:
    main, bin_dir = _setup(tmp_path, "scripts/init-worktree.sh")

    result = _run(main / "scripts/init-worktree.sh", bin_dir)

    assert result.returncode == 0
    assert "无需初始化" in result.stderr
    assert not (main / "deploy").exists()
