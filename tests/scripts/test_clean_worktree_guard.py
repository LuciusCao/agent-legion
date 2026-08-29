"""Contract tests for scripts/clean-worktree.sh caller-cwd guard.

Agents run the teardown script from inside the worktree being cleaned;
without a guard the caller shell's cwd is deleted mid-session and every
later command fails with a stale-cwd error. The tests copy the script into
a synthetic repo layout and run it with stubbed ``git``/``uv``/``psql`` on
a restricted PATH — no real worktree, database, or bucket is touched.
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
SCRIPT = ROOT / "scripts" / "clean-worktree.sh"
DB_SCRIPT = ROOT / "scripts" / "drop-worktree-db.sh"

# git stub: worktree list reports main + .worktrees/{victim,other}; every
# other subcommand is recorded to the stub log so the tests can assert the
# guard fires before any mutating git call. {main} is substituted via
# replace() (not str.format) because the stub itself uses ${VAR:-...}.
_GIT_STUB = """#!/usr/bin/env bash
if [[ "$1" == "worktree" && "$2" == "list" ]]; then
  echo "worktree __MAIN__"
  echo "bare"
  echo
  echo "worktree __MAIN__/.worktrees/victim"
  echo "HEAD 0000000000000000000000000000000000000000"
  echo "branch refs/heads/feat/victim"
  echo
  echo "worktree __MAIN__/.worktrees/other"
  echo "HEAD 0000000000000000000000000000000000000000"
  echo "branch refs/heads/feat/other"
  exit 0
fi
printf 'git %s\\n' "$*" >>"${STUB_LOG:-/dev/null}"
exit 0
"""

# drop-worktree-db.sh runs under psql stubs that report no matching
# databases (safe no-op path).
_PSQL_STUB = """#!/usr/bin/env bash
# every database probe misses -> "没有需要删除的库"
exit 0
"""

_UV_STUB = """#!/usr/bin/env bash
echo "uv stub (S3 cleanup skipped)"
exit 1
"""


def _write_stub(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _setup(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Lay out main/.worktrees/{other,victim}/scripts with the real scripts."""
    main = tmp_path / "main"
    scripts_dir = main / ".worktrees" / "other" / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy(SCRIPT, scripts_dir / "clean-worktree.sh")
    shutil.copy(DB_SCRIPT, scripts_dir / "drop-worktree-db.sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub_log = tmp_path / "stub.log"
    _write_stub(bin_dir / "git", _GIT_STUB.replace("__MAIN__", str(main)))
    _write_stub(bin_dir / "psql", _PSQL_STUB)
    _write_stub(bin_dir / "uv", _UV_STUB)
    return main, bin_dir, stub_log


def _run(
    script_path: Path,
    bin_dir: Path,
    cwd: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": os.environ.get("HOME", ""),
    }
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(script_path), "victim", "--yes"],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        timeout=60,
    )


def test_cwd_inside_target_worktree_is_rejected(tmp_path: Path) -> None:
    main, bin_dir, stub_log = _setup(tmp_path)
    victim = main / ".worktrees" / "victim"
    victim.mkdir(parents=True)
    script_path = main / ".worktrees" / "other" / "scripts" / "clean-worktree.sh"
    stub_log.write_text("", encoding="utf-8")

    result = _run(script_path, bin_dir, cwd=victim, extra_env={"STUB_LOG": str(stub_log)})

    assert result.returncode == 1
    assert "当前 shell 的工作目录在待清理的 worktree 内" in result.stderr
    assert str(victim) in result.stderr
    # The guard fires before any side effect: no git mutation was attempted.
    assert stub_log.read_text(encoding="utf-8") == ""


def test_cwd_in_target_subdirectory_is_rejected(tmp_path: Path) -> None:
    main, bin_dir, stub_log = _setup(tmp_path)
    nested = main / ".worktrees" / "victim" / "scripts"
    nested.mkdir(parents=True)
    script_path = main / ".worktrees" / "other" / "scripts" / "clean-worktree.sh"
    stub_log.write_text("", encoding="utf-8")

    result = _run(script_path, bin_dir, cwd=nested, extra_env={"STUB_LOG": str(stub_log)})

    assert result.returncode == 1
    assert "当前 shell 的工作目录在待清理的 worktree 内" in result.stderr


def test_cwd_outside_target_worktree_proceeds(tmp_path: Path) -> None:
    main, bin_dir, _ = _setup(tmp_path)
    script_dir = main / ".worktrees" / "other"
    script_path = script_dir / "scripts" / "clean-worktree.sh"

    # cwd in a sibling worktree (the invoking agent's own): cleanup proceeds.
    result = _run(script_path, bin_dir, cwd=script_dir)

    assert result.returncode == 0, result.stderr
    assert "收尾清理结束" in result.stdout
    assert "工作目录在待清理的 worktree 内" not in result.stderr
