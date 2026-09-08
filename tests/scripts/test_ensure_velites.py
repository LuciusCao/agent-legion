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
if [[ "$1" == "worktree" && "$2" == "list" ]]; then
  [[ -n "${STUB_WORKTREE_FILE:-}" ]] && cat "${STUB_WORKTREE_FILE}"
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


def _build(root: Path, tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    """Synthetic repo at ``root``: scripts/ensure-velites.sh + velites/, stub bin."""
    script_path = root / "scripts" / "ensure-velites.sh"
    script_path.parent.mkdir(parents=True)
    shutil.copy(SCRIPT, script_path)
    (root / "velites").mkdir()
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
    return root, env, log


def _setup(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    """dev 语境布局：仓库在 tmp_path/main（不在任何 .worktrees/ 下）。"""
    return _build(tmp_path / "main", tmp_path)


def _setup_prod(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    """prod 语境布局：bare 主仓库根 tmp_path/repo + 其 .worktrees/prod 平级
    子目录（git worktree list --porcelain 第一条目是 bare 主根，与
    init-worktree.sh 的判定输入一致）。"""
    root, env, log = _build(tmp_path / "repo" / ".worktrees" / "prod", tmp_path)
    wt_list = tmp_path / "wt-list"
    wt_list.write_text(
        f"worktree {tmp_path / 'repo'}\nbare\n\nworktree {root}\nHEAD 0000000000000000000000000000000000000000\n"
    )
    env["STUB_WORKTREE_FILE"] = str(wt_list)
    return root, env, log


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


# --- worktree 隔离（PR #519 codex P1：dev/prod 共写机器级副本互相污染） ---


def test_prod_worktree_installs_to_isolated_dir(tmp_path: Path) -> None:
    """prod 语境（.worktrees/prod）：安装落到按 worktree 名派生的隔离目录，
    机器级共享位置（VELITES_INSTALL_DIR）不被触碰——「一次开发安装改写
    生产在用二进制」的污染路径从此断开。"""
    main, env, log = _setup_prod(tmp_path)
    env["XDG_DATA_HOME"] = str(tmp_path / "xdg")
    result = _run(main, env)
    assert result.returncode == 0, result.stderr
    isolated = tmp_path / "xdg" / "agent-legion" / "prod" / "bin"
    assert (isolated / "velites").read_text() == "binary-for-hash-v1\n"
    assert (isolated / "velites.src-stamp").read_text() == "hash-v1\n"
    assert not (tmp_path / "install" / "velites").exists()
    assert "cargo build --release --locked" in log.read_text()


def test_print_bin_dir_isolated_wins_over_path_and_override(tmp_path: Path) -> None:
    """--print-bin-dir 在 prod 语境返回隔离目录，且优先于 PATH 已有副本与
    VELITES_INSTALL_DIR——PATH 上的可能是机器级共享副本（dev 维护的），
    prod 不该维护它；native-prod-up.sh 的服务 PATH prepend 随之指向隔离
    目录（单一事实源自动跟随）。"""
    main, env, log = _setup_prod(tmp_path)
    env["XDG_DATA_HOME"] = str(tmp_path / "xdg")
    bin_dir = Path(env["PATH"].split(":")[0])
    _write_stub(bin_dir / "velites", "#!/usr/bin/env bash\n")
    result = _run(main, env, "--print-bin-dir")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(tmp_path / "xdg" / "agent-legion" / "prod" / "bin")
    assert not log.exists()  # 查询形态不触发构建


def test_env_flag_forces_isolation_in_dev_worktree(tmp_path: Path) -> None:
    """AGENT_LEGION_VELITES_ISOLATED=1：非 prod 布局也强制隔离——按 worktree
    名派生目录（想各自私有一份副本的显式开关，目录派生与 prod 同一规则）。"""
    main, env, log = _setup(tmp_path)
    env["XDG_DATA_HOME"] = str(tmp_path / "xdg")
    env["AGENT_LEGION_VELITES_ISOLATED"] = "1"
    result = _run(main, env, "--print-bin-dir")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(tmp_path / "xdg" / "agent-legion" / "main" / "bin")


def test_env_flag_zero_escapes_to_shared_semantics(tmp_path: Path) -> None:
    """=0 显式退回共享语义（prod worktree 的逃生口）：PATH 已有副本 >
    VELITES_INSTALL_DIR 的既有优先级原样生效。"""
    main, env, log = _setup_prod(tmp_path)
    env["AGENT_LEGION_VELITES_ISOLATED"] = "0"
    result = _run(main, env, "--print-bin-dir")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == env["VELITES_INSTALL_DIR"]


def test_invalid_isolation_flag_fails_fast(tmp_path: Path) -> None:
    """非法取值 fail fast（exit 2）：静默按共享处理会让 prod 污染无声回归
    （如把 1 误写成 true）。"""
    main, env, log = _setup(tmp_path)
    env["AGENT_LEGION_VELITES_ISOLATED"] = "true"
    result = _run(main, env, "--print-bin-dir")
    assert result.returncode == 2
    assert "AGENT_LEGION_VELITES_ISOLATED" in result.stderr
    assert "非法" in result.stderr
    assert result.stdout == ""


def test_isolated_install_warns_about_legacy_shared_copy(tmp_path: Path) -> None:
    """迁移提示：隔离形态下机器级共享位置已存在的副本不被代删，stderr 提示
    其不再被本 worktree 使用并附手动清理指引（与 data/bin 存量提示同款手法）；
    PATH prepend 保证服务解析隔离副本，旧副本只是无主文件风险。"""
    main, env, log = _setup_prod(tmp_path)
    env["XDG_DATA_HOME"] = str(tmp_path / "xdg")
    legacy = Path(env["VELITES_INSTALL_DIR"]) / "velites"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("legacy-shared-copy\n")
    result = _run(main, env)
    assert result.returncode == 0, result.stderr
    assert "机器级共享副本" in result.stderr
    assert f"rm -f {legacy}" in result.stderr
    # 不代删：旧副本原样保留
    assert legacy.read_text() == "legacy-shared-copy\n"


def test_isolated_warning_names_resolved_shared_copy(tmp_path: Path) -> None:
    """隔离形态的解析守门：shell 解析到非本 worktree 的副本（典型：机器级
    共享副本）时，警告点名当前解析到的路径并说明版本来源差异——而非笼统的
    「解析不到」；服务链经 prepend 不受影响是必须给出的定心丸。"""
    main, env, log = _setup_prod(tmp_path)
    env["XDG_DATA_HOME"] = str(tmp_path / "xdg")
    bin_dir = Path(env["PATH"].split(":")[0])
    _write_stub(bin_dir / "velites", "#!/usr/bin/env bash\n")
    result = _run(main, env)
    assert result.returncode == 0, result.stderr
    assert "隔离副本" in result.stderr
    assert str(bin_dir / "velites") in result.stderr
    assert "非本 worktree 的隔离副本" in result.stderr
    assert "服务链不受影响" in result.stderr


def test_dev_does_not_maintain_foreign_isolated_copy_on_path(tmp_path: Path) -> None:
    """反向污染防护（dev 语境）：PATH 解析到的 velites 位于隔离目录树（其他
    worktree 的私有运行时，典型：用户手工把 prod 的隔离目录加进 PATH）时不
    维护它——开发构建写进生产运行时副本正是本 issue 要修的污染，只是方向
    相反。跳过后回落共享语义（VELITES_INSTALL_DIR）。"""
    main, env, log = _setup(tmp_path)
    xdg = tmp_path / "xdg"
    foreign = xdg / "agent-legion" / "prod" / "bin"
    foreign.mkdir(parents=True)
    _write_stub(foreign / "velites", "#!/usr/bin/env bash\n")
    env["XDG_DATA_HOME"] = str(xdg)
    env["PATH"] = f"{foreign}:{env['PATH']}"
    result = _run(main, env, "--print-bin-dir")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == env["VELITES_INSTALL_DIR"]
    # 安装形态同样不写隔离树：产物落共享位置，隔离树里的副本原样不动。
    result = _run(main, env)
    assert result.returncode == 0, result.stderr
    assert (Path(env["VELITES_INSTALL_DIR"]) / "velites").read_text() == "binary-for-hash-v1\n"
    assert (foreign / "velites").read_text() == "#!/usr/bin/env bash\n"
