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


def _run(main: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/ensure-velites.sh", *args],
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


def test_dest_installs_into_target_dir_regardless_of_path(tmp_path: Path) -> None:
    """--dest DIR：跳过 PATH 探测，安装到 DIR/velites（Worker 自带副本通道）。"""
    main, env, log = _setup(tmp_path)
    # PATH 上放一个既有 velites，验证 --dest 不落在它上面。
    path_velites = Path(env["PATH"].split(":")[0]) / "velites"
    _write_stub(path_velites, "#!/usr/bin/env bash\n")
    result = _run(main, env, "--dest", "data/bin")
    assert result.returncode == 0, result.stderr
    bundled = main / "data" / "bin" / "velites"
    assert bundled.read_text() == "binary-for-hash-v1\n"
    assert (main / "data" / "bin" / "velites.src-stamp").read_text() == "hash-v1\n"
    assert "cargo build --release --locked" in log.read_text()


def test_dest_skips_rebuild_when_stamp_matches(tmp_path: Path) -> None:
    main, env, log = _setup(tmp_path)
    assert _run(main, env, "--dest", "data/bin").returncode == 0
    log.write_text("")
    result = _run(main, env, "--dest", "data/bin")
    assert result.returncode == 0, result.stderr
    assert "跳过构建" in result.stdout
    assert log.read_text() == ""


def test_prod_up_sequence_refreshes_stale_bundled_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#831 回归：PATH 副本新、data/bin 副本旧 → prod-up 的双通道刷新后，
    Worker（自带副本优先解析）拿到的必须是刷新后的 data/bin 版本。

    布局复现原生生产现场：install-deps 首次安置 data/bin 副本（hash-v1），
    仓库跨版本线后旧 prod-up 只刷 PATH 副本（hash-v3）——自带副本优先的
    解析语义让 Worker 静默滞留在 v1。新 prod-up 按 native-prod-up.sh 的
    调用序列（PATH 模式 + --dest data/bin）执行后，两处副本都必须是 v3。"""

    from shared import code_sandbox
    from worker.binary_resolution import resolve_binary

    main, env, log = _setup(tmp_path)
    bundled_dir = main / "data" / "bin"
    install = tmp_path / "install"

    # 首次安装（install-deps.sh 通道）：data/bin 副本 = hash-v1
    assert _run(main, env, "--dest", "data/bin").returncode == 0
    assert (bundled_dir / "velites").read_text() == "binary-for-hash-v1\n"

    # 仓库前进到 hash-v3；旧 prod-up（仅 PATH 模式）只刷新了 PATH 副本
    Path(env["STUB_HASH_FILE"]).write_text("hash-v3\n")
    assert _run(main, env).returncode == 0
    assert (install / "velites").read_text() == "binary-for-hash-v3\n"
    # 自带副本仍滞留 v1：PATH 刷新对「自带副本优先」的解析不生效（#831 现象）
    assert (bundled_dir / "velites").read_text() == "binary-for-hash-v1\n"

    # 新 prod-up 的调用序列：两通道都跑 → data/bin 副本刷新到 v3
    assert _run(main, env).returncode == 0
    assert _run(main, env, "--dest", "data/bin").returncode == 0
    assert (bundled_dir / "velites").read_text() == "binary-for-hash-v3\n"
    assert (bundled_dir / "velites.src-stamp").read_text() == "hash-v3\n"

    # Worker 解析（自带副本优先、PATH 兜底）落在刷新后的 v3 副本上
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", bundled_dir)
    monkeypatch.setattr(shutil, "which", lambda _binary: str(install / "velites"))
    resolved = resolve_binary("velites")
    assert resolved == str(bundled_dir / "velites")
    assert Path(resolved).read_text() == "binary-for-hash-v3\n"
