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


# --- --print-bin-dir：安装目录查询通道（服务启动器 prepend PATH 的单一事实源） ---


def test_print_bin_dir_defaults_to_install_dir_without_path_velites(tmp_path: Path) -> None:
    """--print-bin-dir：PATH 无 velites → 输出 VELITES_INSTALL_DIR（默认
    ~/.local/bin 的覆盖位）。查询形态不触发 git 探测/构建，可直接在无工具链
    环境调用。"""
    main, env, log = _setup(tmp_path)
    result = _run(main, env, "--print-bin-dir")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == env["VELITES_INSTALL_DIR"]
    assert not log.exists()  # 查询形态不触发构建


def test_print_bin_dir_follows_path_velites_and_wins_over_override(tmp_path: Path) -> None:
    """--print-bin-dir：PATH 上已有 velites → 输出其所在目录，且优先于
    VELITES_INSTALL_DIR——与无参安装形态维护既有副本所在地的决策一致。"""
    main, env, log = _setup(tmp_path)
    bin_dir = Path(env["PATH"].split(":")[0])
    _write_stub(bin_dir / "velites", "#!/usr/bin/env bash\n")
    result = _run(main, env, "--print-bin-dir")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(bin_dir)
    assert not log.exists()  # 查询形态不触发构建


# --- 安装侧 PATH 守门（PR #519 codex P1：装了但当前环境解析不到不再是静默态） ---


def test_install_warns_when_install_dir_not_on_path(tmp_path: Path) -> None:
    """安装目录不在调用方 PATH → stderr 打明确指引（含 export PATH 提示），
    退出码仍为 0——保留 data/bin 存量副本的机器可能仍靠兜底在服务，交互
    场景不误伤。"""
    main, env, log = _setup(tmp_path)
    result = _run(main, env)
    assert result.returncode == 0, result.stderr
    assert "不在当前 PATH" in result.stderr
    assert "export PATH=" in result.stderr


def test_warning_repeats_on_freshness_skip(tmp_path: Path) -> None:
    """指纹一致跳过构建时守门同样生效——「装了但解析不到」不能因跳过构建
    而被掩盖（升级机器的常态路径：pull 后指纹一致，但 shell 一直没配 PATH）。"""
    main, env, log = _setup(tmp_path)
    assert _run(main, env).returncode == 0
    result = _run(main, env)
    assert result.returncode == 0, result.stderr
    assert "跳过构建" in result.stdout
    assert "不在当前 PATH" in result.stderr


def test_no_warning_when_install_dir_on_path(tmp_path: Path) -> None:
    """安装目录已在 PATH 上（command -v 命中刚装的副本）→ 无警告。"""
    main, env, log = _setup(tmp_path)
    assert _run(main, env).returncode == 0
    env["PATH"] = f"{env['PATH']}:{env['VELITES_INSTALL_DIR']}"
    log.write_text("")
    result = _run(main, env)
    assert result.returncode == 0, result.stderr
    assert "不在当前 PATH" not in result.stderr
    assert "跳过构建" in result.stdout


def test_dest_install_never_warns_about_path(tmp_path: Path) -> None:
    """--dest 是显式安置通道（Docker 外挂/compose VELITES_BIN），不期望
    PATH 命中，守门不触发。"""
    main, env, log = _setup(tmp_path)
    result = _run(main, env, "--dest", "data/bin")
    assert result.returncode == 0, result.stderr
    assert "不在当前 PATH" not in result.stderr
