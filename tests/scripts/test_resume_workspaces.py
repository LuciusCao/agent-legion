"""Contract tests for scripts/resume-workspaces.sh.

The script resolves ROOT from its own location, so tests copy it into a
synthetic worktree layout and run it with a stubbed ``uv`` on a restricted
PATH: no real database is touched.
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
SCRIPT = ROOT / "scripts" / "resume-workspaces.sh"

_UV_STUB = """#!/usr/bin/env bash
echo "AGENT_LEGION_DATABASE_URL=${AGENT_LEGION_DATABASE_URL-<unset>}" >> "${STUB_LOG:-/dev/null}"
"""

_UV_STUB_FAIL = """#!/usr/bin/env bash
echo "stub: relation \\"workspaces\\" does not exist" >&2
exit 1
"""


def _write_stub(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _setup(tmp_path: Path, uv_stub: str = _UV_STUB, *, with_env: bool = True) -> tuple[Path, Path]:
    """Lay out <worktree>/scripts/resume-workspaces.sh plus a stub bin dir."""
    worktree = tmp_path / "flat"
    script_path = worktree / "scripts" / "resume-workspaces.sh"
    script_path.parent.mkdir(parents=True)
    shutil.copy(SCRIPT, script_path)
    if with_env:
        (worktree / ".env").write_text(
            "AGENT_LEGION_DATABASE_URL=postgresql://127.0.0.1:5432/agent_legion_flat\n"
        )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(bin_dir / "uv", uv_stub)
    return worktree, bin_dir


def _run(
    script_path: Path,
    bin_dir: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": os.environ.get("HOME", "")}
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_resume_uses_env_database_url_not_inherited_env(tmp_path: Path) -> None:
    """恢复子进程必须用 .env 里的专属 URL，而不是调用 shell 已导出的
    AGENT_LEGION_DATABASE_URL（load_dotenv override=False 会保留它）。"""
    worktree, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "uv-env.log"

    result = _run(
        worktree / "scripts" / "resume-workspaces.sh",
        bin_dir,
        extra_env={
            "AGENT_LEGION_DATABASE_URL": "postgresql://127.0.0.1:5432/agent_legion_base",
            "STUB_LOG": str(stub_log),
        },
    )

    assert result.returncode == 0, result.stderr
    assert stub_log.read_text().splitlines() == [
        "AGENT_LEGION_DATABASE_URL=postgresql://127.0.0.1:5432/agent_legion_flat"
    ]


def test_resume_derives_url_from_worktree_name_when_env_missing(tmp_path: Path) -> None:
    """.env 缺 AGENT_LEGION_DATABASE_URL 时按 worktree 目录名派生（与
    init-worktree.sh 同一规则）。"""
    worktree, bin_dir = _setup(tmp_path, with_env=False)
    stub_log = tmp_path / "uv-env.log"

    result = _run(
        worktree / "scripts" / "resume-workspaces.sh",
        bin_dir,
        extra_env={"STUB_LOG": str(stub_log)},
    )

    assert result.returncode == 0, result.stderr
    assert stub_log.read_text().splitlines() == [
        "AGENT_LEGION_DATABASE_URL=postgresql://127.0.0.1:5432/agent_legion_flat"
    ]


def test_resume_fails_loudly_with_hint_when_backend_not_ready(tmp_path: Path) -> None:
    """后端尚未首次启动建表时：退出码 1 且给出可操作提示。"""
    worktree, bin_dir = _setup(tmp_path, uv_stub=_UV_STUB_FAIL)

    result = _run(worktree / "scripts" / "resume-workspaces.sh", bin_dir)

    assert result.returncode == 1
    assert "尚未首次启动建表" in result.stderr
