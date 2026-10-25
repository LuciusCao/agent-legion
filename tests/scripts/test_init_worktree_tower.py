"""Contract tests for tower worktree initialization (#1409).

Tower children reuse their registered parent worktree's database, bucket and
vault. Synthetic repositories and command stubs keep the real runtime untouched.
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
SCRIPTS = (
    SCRIPT,
    ROOT / "scripts" / "ensure-s3-bucket.py",
    ROOT / "scripts" / "uv_cache_prewarm.py",
    ROOT / "scripts" / "worktree-names-lib.sh",
)

_GIT_STUB = """#!/usr/bin/env bash
if [[ "$1" == "worktree" && "$2" == "list" ]]; then
  echo "worktree {main}"
  echo "bare"
  echo
  echo "worktree {main}/.worktrees/develop"
  echo "HEAD 0000000000000000000000000000000000000000"
  echo "branch refs/heads/main"
  echo
  echo "worktree {main}/.worktrees/develop/.tower/worktrees/wt-101"
  echo "HEAD 1111111111111111111111111111111111111111"
  echo "branch refs/heads/feat/tower"
  exit 0
fi
if [[ "$1" == "rev-parse" && "$2" == "--git-common-dir" ]]; then
  echo "{main}/.git"
  exit 0
fi
echo "unexpected git call: $*" >&2
exit 1
"""

_UV_STUB = """#!/usr/bin/env bash
echo "uv $*" >> "${STUB_LOG:-/dev/null}"
echo "unexpected-generated-vault-key"
"""

_COMMAND_PROBE = """#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "${STUB_LOG:-/dev/null}"
exit 1
"""


def _write_stub(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _setup(
    tmp_path: Path,
    script_rel: str = ".worktrees/develop/.tower/worktrees/wt-101/scripts/init-worktree.sh",
) -> tuple[Path, Path, Path]:
    main = tmp_path / "main"
    script_path = main / script_rel
    script_path.parent.mkdir(parents=True)
    (main / ".git").mkdir()
    for source in SCRIPTS:
        shutil.copy(source, script_path.parent / source.name)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(bin_dir / "git", _GIT_STUB.format(main=main))
    _write_stub(bin_dir / "uv", _UV_STUB)
    _write_stub(bin_dir / "createdb", _COMMAND_PROBE)
    _write_stub(bin_dir / "psql", _COMMAND_PROBE)
    return main, bin_dir, tmp_path / "stub.log"


def _parent(main: Path) -> Path:
    return main / ".worktrees" / "develop"


def _child(main: Path) -> Path:
    return _parent(main) / ".tower" / "worktrees" / "wt-101"


def _seed_parent(main: Path, *, env: bool = True, vault: bool = True) -> None:
    parent = _parent(main)
    if env:
        (parent / ".env").write_text(
            "AGENT_LEGION_DATABASE_URL=postgresql://127.0.0.1:5432/agent_legion_develop\n"
            "AGENT_LEGION_S3_BUCKET=agent-legion-develop\n"
            "PARENT_ONLY_SETTING=preserved\n",
            encoding="utf-8",
        )
    if vault:
        key = parent / "deploy" / "secrets" / "vault_master_key"
        key.parent.mkdir(parents=True, exist_ok=True)
        key.write_text("parent-vault-master-key\n", encoding="utf-8")


def _run(script_path: Path, bin_dir: Path, stub_log: Path) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": os.environ.get("HOME", ""),
        "STUB_LOG": str(stub_log),
    }
    return subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_tower_child_reuses_parent_resources_and_secrets(tmp_path: Path) -> None:
    main, bin_dir, stub_log = _setup(tmp_path)
    _seed_parent(main)
    state = _parent(main) / "data" / "agent-worker-service" / "worker.yaml"
    state.parent.mkdir(parents=True)
    state.write_text(
        "host_url: http://127.0.0.1:8017\n"
        "worker_id: develop\n"
        "name: develop (worktree)\n"
        "register_token_file: /private/parent/token\n",
        encoding="utf-8",
    )

    result = _run(_child(main) / "scripts" / "init-worktree.sh", bin_dir, stub_log)

    assert result.returncode == 0, result.stderr
    child = _child(main)
    assert (child / ".env").read_text() == (_parent(main) / ".env").read_text()
    assert (child / "deploy" / "secrets" / "vault_master_key").read_text() == (
        "parent-vault-master-key\n"
    )
    worker = (child / "data" / "agent-worker-service" / "worker.yaml").read_text()
    assert "host_url: http://127.0.0.1:8017" in worker
    assert "worker_id: wt_101" in worker
    assert "name: wt_101 (worktree)" in worker
    assert "register_token_file" not in worker
    assert not (child / ".worktree-initialized.ok").exists()
    assert not stub_log.exists() or stub_log.read_text() == ""
    assert "不创建独立库" in result.stdout
    assert "不创建独立 bucket" in result.stdout


def test_tower_child_initialization_is_idempotent(tmp_path: Path) -> None:
    main, bin_dir, stub_log = _setup(tmp_path)
    _seed_parent(main)
    script = _child(main) / "scripts" / "init-worktree.sh"

    first = _run(script, bin_dir, stub_log)
    assert first.returncode == 0, first.stderr
    child = _child(main)
    before = {
        path: path.read_bytes()
        for path in (
            child / ".env",
            child / "deploy" / "secrets" / "vault_master_key",
        )
    }
    second = _run(script, bin_dir, stub_log)

    assert second.returncode == 0, second.stderr
    for path, content in before.items():
        assert path.read_bytes() == content
    assert not stub_log.exists() or stub_log.read_text() == ""


def test_tower_child_fails_before_side_effects_when_parent_env_is_missing(tmp_path: Path) -> None:
    main, bin_dir, stub_log = _setup(tmp_path)
    _seed_parent(main, env=False)

    result = _run(_child(main) / "scripts" / "init-worktree.sh", bin_dir, stub_log)

    assert result.returncode == 1
    assert "父 worktree 缺少 .env" in result.stderr
    child = _child(main)
    assert not (child / ".env").exists()
    assert not (child / "deploy").exists()
    assert not (child / "data").exists()


def test_tower_child_fails_when_parent_vault_key_is_missing(tmp_path: Path) -> None:
    main, bin_dir, stub_log = _setup(tmp_path)
    _seed_parent(main, vault=False)

    result = _run(_child(main) / "scripts" / "init-worktree.sh", bin_dir, stub_log)

    assert result.returncode == 1
    assert "缺少 vault_master_key" in result.stderr
    assert not (_child(main) / ".env").exists()
    assert not (_child(main) / "deploy").exists()


@pytest.mark.parametrize(
    "worktree_rel",
    [
        ".worktrees/develop/.tower/worktrees/feature",
        ".worktrees/develop/.tower/worktrees/wt-101/nested",
    ],
)
def test_tower_lookalike_directory_is_still_rejected_as_nested(
    tmp_path: Path, worktree_rel: str
) -> None:
    script_rel = f"{worktree_rel}/scripts/init-worktree.sh"
    main, bin_dir, stub_log = _setup(tmp_path, script_rel)

    result = _run(main / script_rel, bin_dir, stub_log)

    assert result.returncode == 1
    assert "禁止嵌套" in result.stderr
    assert not (main / worktree_rel / "deploy").exists()


def test_tower_child_rejects_an_explicit_non_parent_base(tmp_path: Path) -> None:
    main, bin_dir, stub_log = _setup(tmp_path)
    _seed_parent(main)

    result = subprocess.run(
        ["bash", str(_child(main) / "scripts" / "init-worktree.sh"), str(main / "elsewhere")],
        capture_output=True,
        text=True,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "STUB_LOG": str(stub_log)},
        timeout=60,
    )

    assert result.returncode == 1
    assert "不能指定其他基准" in result.stderr
    assert not (_child(main) / ".env").exists()
