"""Contract tests for scripts/ensure-velites.sh staleness detection.

The script resolves ROOT from its own location, so tests copy it into a
synthetic repo layout and run it with stubbed ``git``/``cargo`` on a
restricted PATH: no real repo, cargo build, or PATH velites is touched.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ensure-velites.sh"

_GIT_STUB = """#!/usr/bin/env bash
if [[ "$1" == "rev-parse" && "$2" == "HEAD:velites" ]]; then
  cat "${STUB_HASH_FILE}"
  exit 0
fi
if [[ "$1" == "status" ]]; then
  cat "${STUB_STATUS_FILE}"
  exit 0
fi
echo "unexpected git call: $*" >&2
exit 1
"""

# cargo runs with cwd=<root>/velites (the script cd's there before building).
_CARGO_STUB = """#!/usr/bin/env bash
echo "cargo $*" >> "${STUB_LOG}"
mkdir -p target/release
echo "binary-for-$(cat "${STUB_HASH_FILE}")" > target/release/velites
"""


def _write_stub(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _setup(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    """Synthetic repo: main/scripts/ensure-velites.sh + main/velites/, stub bin."""
    main = tmp_path / "main"
    script_path = main / "scripts" / "ensure-velites.sh"
    script_path.parent.mkdir(parents=True)
    shutil.copy(SCRIPT, script_path)
    (main / "velites").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    hash_file = tmp_path / "src-hash"
    hash_file.write_text("hash-v1\n")
    status_file = tmp_path / "src-status"
    status_file.write_text("")
    log = tmp_path / "stub.log"
    _write_stub(bin_dir / "git", _GIT_STUB)
    _write_stub(bin_dir / "cargo", _CARGO_STUB)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "STUB_HASH_FILE": str(hash_file),
        "STUB_STATUS_FILE": str(status_file),
        "STUB_LOG": str(log),
        "VELITES_INSTALL_DIR": str(tmp_path / "install"),
    }
    return main, env, log


def _run(main: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/ensure-velites.sh"],
        cwd=main,
        env=env,
        capture_output=True,
        text=True,
    )


def _installed(tmp_path: Path) -> tuple[Path, Path]:
    install = tmp_path / "install"
    return install / "velites", install / "velites.src-stamp"


def test_builds_and_installs_when_binary_missing(tmp_path: Path) -> None:
    main, env, log = _setup(tmp_path)
    result = _run(main, env)
    assert result.returncode == 0, result.stderr
    binary, stamp = _installed(tmp_path)
    assert binary.read_text() == "binary-for-hash-v1\n"
    assert stamp.read_text() == "hash-v1\n"
    assert "cargo build --release --locked" in log.read_text()


def test_skips_when_stamp_matches_source(tmp_path: Path) -> None:
    main, env, log = _setup(tmp_path)
    assert _run(main, env).returncode == 0
    log.write_text("")  # reset cargo invocation log
    result = _run(main, env)
    assert result.returncode == 0, result.stderr
    assert "跳过构建" in result.stdout
    assert log.read_text() == ""


def test_rebuilds_when_source_hash_changes(tmp_path: Path) -> None:
    main, env, log = _setup(tmp_path)
    assert _run(main, env).returncode == 0
    Path(env["STUB_HASH_FILE"]).write_text("hash-v2\n")
    result = _run(main, env)
    assert result.returncode == 0, result.stderr
    binary, stamp = _installed(tmp_path)
    assert binary.read_text() == "binary-for-hash-v2\n"
    assert stamp.read_text() == "hash-v2\n"


def test_rebuilds_when_stamp_matches_but_binary_deleted(tmp_path: Path) -> None:
    main, env, log = _setup(tmp_path)
    assert _run(main, env).returncode == 0
    binary, stamp = _installed(tmp_path)
    binary.unlink()
    result = _run(main, env)
    assert result.returncode == 0, result.stderr
    assert binary.exists()


def test_dirty_tree_forces_rebuild_even_with_matching_stamp(tmp_path: Path) -> None:
    main, env, log = _setup(tmp_path)
    assert _run(main, env).returncode == 0
    Path(env["STUB_STATUS_FILE"]).write_text(" M velites/src/main.rs\n")
    log.write_text("")
    result = _run(main, env)
    assert result.returncode == 0, result.stderr
    assert "强制重新构建" in result.stdout
    assert "cargo build" in log.read_text()


def test_fails_when_rebuild_needed_but_cargo_missing(tmp_path: Path) -> None:
    main, env, log = _setup(tmp_path)
    (Path(env["PATH"].split(":")[0]) / "cargo").unlink()
    result = _run(main, env)
    assert result.returncode == 1
    assert "cargo 不可用" in result.stderr
