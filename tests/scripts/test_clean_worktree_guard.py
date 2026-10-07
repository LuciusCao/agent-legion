"""Contract tests for scripts/clean-worktree.sh guards.

Covers the caller-cwd guard, the remote-branch protection list (#930) and
the worktree-name validation / derived-name parity (#587).

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
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "clean-worktree.sh"
DB_SCRIPT = ROOT / "scripts" / "drop-worktree-db.sh"
# 派生名与撞名检测的共享库（#950），两个脚本都 source 它。
NAMES_LIB = ROOT / "scripts" / "worktree-names-lib.sh"

# git stub: worktree list reports main + .worktrees/{victim,other}; every
# other subcommand is recorded to the stub log so the tests can assert the
# guard fires before any mutating git call. {main} is substituted via
# replace() (not str.format) because the stub itself uses ${VAR:-...}.
# The listing is emitted with ONE printf (a single pipe write, like real git's
# buffered stdout): the script reads it via `... | awk '{...; exit}'` under
# pipefail, and per-line echoes would race awk's early exit into SIGPIPE.
_GIT_STUB = """#!/usr/bin/env bash
if [[ "$1" == "worktree" && "$2" == "list" ]]; then
  printf '%s\\n' \\
    "worktree __MAIN__" \\
    "bare" \\
    "" \\
    "worktree __MAIN__/.worktrees/${STUB_WT:-victim}" \\
    "HEAD 0000000000000000000000000000000000000000" \\
    "branch refs/heads/${STUB_BRANCH:-feat/victim}" \\
    "" \\
    "worktree __MAIN__/.worktrees/other" \\
    "HEAD 0000000000000000000000000000000000000000" \\
    "branch refs/heads/feat/other"
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
    shutil.copy(NAMES_LIB, scripts_dir / NAMES_LIB.name)
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
    args: tuple[str, ...] = ("victim", "--yes"),
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": os.environ.get("HOME", ""),
    }
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(script_path), *args],
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


# --- remote-branch protection (#930) ---------------------------------------


@pytest.mark.parametrize(
    "branch",
    ["main", "master", "develop", "prod", "release/0.7.16", "release/next"],
)
def test_delete_remote_branch_refuses_protected_branches(tmp_path: Path, branch: str) -> None:
    main, bin_dir, stub_log = _setup(tmp_path)
    (main / ".worktrees" / "victim").mkdir(parents=True)
    script_dir = main / ".worktrees" / "other"
    stub_log.write_text("", encoding="utf-8")

    result = _run(
        script_dir / "scripts" / "clean-worktree.sh",
        bin_dir,
        cwd=main,
        extra_env={"STUB_LOG": str(stub_log), "STUB_BRANCH": branch},
        args=("victim", "--yes", "--delete-remote-branch"),
    )

    assert result.returncode == 0, result.stderr
    assert f"{branch} 是受保护分支" in result.stderr
    log = stub_log.read_text(encoding="utf-8")
    assert "push" not in log
    # Without the flag no delete hint is printed for a protected branch either.
    result = _run(
        script_dir / "scripts" / "clean-worktree.sh",
        bin_dir,
        cwd=main,
        extra_env={"STUB_LOG": str(stub_log), "STUB_BRANCH": branch},
    )
    assert result.returncode == 0, result.stderr
    assert "git push origin --delete" not in result.stdout


@pytest.mark.parametrize("branch", ["feat/victim", "fix/release-notes", "releases/x", "mainline"])
def test_delete_remote_branch_deletes_unprotected_branch(tmp_path: Path, branch: str) -> None:
    main, bin_dir, stub_log = _setup(tmp_path)
    (main / ".worktrees" / "victim").mkdir(parents=True)
    stub_log.write_text("", encoding="utf-8")

    result = _run(
        main / ".worktrees" / "other" / "scripts" / "clean-worktree.sh",
        bin_dir,
        cwd=main,
        extra_env={"STUB_LOG": str(stub_log), "STUB_BRANCH": branch},
        args=("victim", "--yes", "--delete-remote-branch"),
    )

    assert result.returncode == 0, result.stderr
    assert f"git push origin --delete {branch}\n" in stub_log.read_text(encoding="utf-8")
    assert "受保护分支" not in result.stderr


# --- worktree-name validation and derived-name parity (#587) ---------------

_INIT_SCRIPT = ROOT / "scripts" / "init-worktree.sh"

# Fakes for the S3 heredoc in clean-worktree.sh: the uv stub runs the heredoc
# with the real python, these modules shadow dotenv/boto3/botocore and the
# repo modules (PYTHONPATH is the synthetic ROOT), and head_bucket always
# reports 404 so the script prints the derived bucket name and exits 0.
_FAKE_MODULES = {
    "dotenv.py": "def load_dotenv(*a, **k):\n    return False\n",
    "botocore/__init__.py": "",
    "botocore/exceptions.py": (
        "class ClientError(Exception):\n"
        "    def __init__(self, response):\n"
        "        super().__init__('stub')\n"
        "        self.response = response\n"
        "class EndpointConnectionError(Exception):\n"
        "    pass\n"
    ),
    "boto3.py": (
        "from botocore.exceptions import ClientError\n"
        "class _Client:\n"
        "    def head_bucket(self, Bucket):\n"
        "        raise ClientError({'Error': {'Code': '404'}})\n"
        "def client(*a, **k):\n"
        "    return _Client()\n"
    ),
    "scripts/__init__.py": "",
    "scripts/seaweedfs_collection.py": (
        "class CollectionGuardError(Exception):\n    pass\n"
        "def leftover_volume_ids(*a):\n    return ()\n"
        "def manual_reclaim_command(*a):\n    return ''\n"
        "def resolve_master_url(*a):\n    return None\n"
    ),
    "server/__init__.py": "",
    "server/app/__init__.py": "",
    "server/app/storage.py": (
        "class _S:\n"
        "    region = 'us-east-1'\n"
        "    endpoint_url = None\n"
        "    access_key = None\n"
        "    secret_key = None\n"
        "def load_s3_settings():\n    return _S()\n"
    ),
}


def _install_s3_fakes(main: Path, bin_dir: Path) -> None:
    root = main / ".worktrees" / "other"
    for rel, content in _FAKE_MODULES.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _write_stub(bin_dir / "uv", f'#!/usr/bin/env bash\nexec "{sys.executable}" -\n')


def _init_derivations(name: str) -> tuple[str, str]:
    """Evaluate init-worktree.sh's own DB/BUCKET lines for a worktree name."""
    lines = _INIT_SCRIPT.read_text(encoding="utf-8").splitlines()
    wanted = [ln for ln in lines if ln.startswith(("DB=", "BUCKET="))]
    assert len(wanted) == 2, wanted
    snippet = "\n".join(
        [
            f'source "{NAMES_LIB}"',
            f'ROOT="/x/{name}"',
            *wanted,
            'printf "%s\\n%s\\n" "$DB" "$BUCKET"',
        ]
    )
    out = subprocess.run(["bash", "-c", snippet], capture_output=True, text=True, check=True)
    db, bucket = out.stdout.splitlines()
    return db, bucket


@pytest.mark.parametrize("name", ["release-0.7.8", "Rel.716.x", "a..b"])
def test_dotted_worktree_name_derives_same_names_as_init(tmp_path: Path, name: str) -> None:
    main, bin_dir, stub_log = _setup(tmp_path)
    (main / ".worktrees" / name).mkdir(parents=True)
    _install_s3_fakes(main, bin_dir)
    stub_log.write_text("", encoding="utf-8")

    result = _run(
        main / ".worktrees" / "other" / "scripts" / "clean-worktree.sh",
        bin_dir,
        cwd=main,
        extra_env={"STUB_LOG": str(stub_log), "STUB_WT": name},
        args=(name, "--yes"),
    )

    assert result.returncode == 0, result.stderr
    assert f"git worktree remove {main}/.worktrees/{name}\n" in stub_log.read_text(encoding="utf-8")
    init_db, init_bucket = _init_derivations(name)
    test_db = "agent_legion_test_" + init_db.removeprefix("agent_legion_").lower()
    assert f"不存在（跳过）: {init_db}\n" in result.stdout
    assert f"不存在（跳过）: {test_db}\n" in result.stdout
    assert f"S3 bucket 不存在（跳过）: {init_bucket}\n" in result.stdout
    # '.' is folded away: derived names keep their guarded prefixes and never
    # collapse onto a shared/prod name.
    assert "." not in init_db and "." not in init_bucket
    assert init_db.startswith("agent_legion_") and init_bucket.startswith("agent-legion-")
    assert init_db not in {"agent_legion", "agent_legion_prod", "agent_legion_develop"}
    assert init_bucket not in {"agent-legion", "agent-legion-prod", "agent-legion-develop"}


_BAD_NAMES = [".", "..", ".hidden", "../x", "a/../b", "a/b", "-x", "", "a b", "x/.."]


@pytest.mark.parametrize("name", _BAD_NAMES)
def test_clean_worktree_rejects_path_like_names(tmp_path: Path, name: str) -> None:
    main, bin_dir, stub_log = _setup(tmp_path)
    stub_log.write_text("", encoding="utf-8")

    result = _run(
        main / ".worktrees" / "other" / "scripts" / "clean-worktree.sh",
        bin_dir,
        cwd=main,
        extra_env={"STUB_LOG": str(stub_log)},
        args=(name, "--yes"),
    )

    assert result.returncode == 1
    assert "非法 worktree 名" in result.stderr
    assert stub_log.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("name", _BAD_NAMES)
def test_drop_worktree_db_rejects_path_like_names(tmp_path: Path, name: str) -> None:
    _, bin_dir, _ = _setup(tmp_path)

    result = _run(DB_SCRIPT, bin_dir, cwd=tmp_path, args=(name, "--yes"))

    assert result.returncode == 1
    assert "非法 worktree 名" in result.stderr


def test_drop_worktree_db_accepts_dotted_name(tmp_path: Path) -> None:
    _, bin_dir, _ = _setup(tmp_path)

    result = _run(DB_SCRIPT, bin_dir, cwd=tmp_path, args=("release-0.7.8", "--yes"))

    assert result.returncode == 0, result.stderr
    assert "不存在（跳过）: agent_legion_release_0_7_8\n" in result.stdout
    assert "不存在（跳过）: agent_legion_test_release_0_7_8\n" in result.stdout
