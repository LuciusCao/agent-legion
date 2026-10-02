"""Contract tests for scripts/ensure-velites.sh staleness detection.

The script resolves ROOT from its own location, so tests copy it (plus the
planner and its python import surface) into a synthetic repo layout and run
it with stubbed ``git``/``cargo`` on a restricted PATH: no real repo, cargo
build, or PATH velites is touched. ``VELITES_PLAN_PYTHON`` points the script
at the repo venv interpreter — the synthetic repo carries no venv of its own.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ensure-velites.sh"
PLANNER = ROOT / "scripts" / "velites_deploy_plan.py"

#: planner 的 import 面（真实 resolver 链）：合成 repo 里复刻这份布局，
#: planner 以真实代码推导安置目标——这正是被测语义（脚本不再持有自己的
#: 查找模型，#835 的根源修复）。
_PLANNER_PY_IMPORTS = (
    "shared/__init__.py",
    "shared/code_sandbox.py",
    "shared/code_contract.py",
    "shared/velites_staleness.py",
    "worker/__init__.py",
    "worker/binary_resolution.py",
    "worker/runtime/__init__.py",
    "worker/runtime/catalog.py",
)

#: 合成 repo 的 velites/Cargo.toml：与真实清单同构（velites /
#: velites-sandbox / velites-schema），第三个验证「无消费者的 bin 不被
#: 强制安置」。
_CARGO_TOML = """\
[package]
name = "velites"
version = "0.5.5"

[[bin]]
name = "velites"
path = "src/main.rs"

[[bin]]
name = "velites-sandbox"
path = "src/bin/velites_sandbox.rs"

[[bin]]
name = "velites-schema"
path = "src/bin/velites_schema.rs"
"""

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

# cargo runs with cwd=<root>/velites (the script cd's there before building);
# 同批产出家族 bin（planner 的安置目标依赖它们存在）。
_CARGO_STUB = """#!/usr/bin/env bash
echo "cargo $*" >> "${STUB_LOG}"
mkdir -p target/release
echo "binary-for-$(cat "${STUB_HASH_FILE}")" > target/release/velites
echo "sandbox-for-$(cat "${STUB_HASH_FILE}")" > target/release/velites-sandbox
"""


def _write_stub(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _setup(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    """Synthetic repo: script + planner + resolver import surface + stubs."""
    main = tmp_path / "main"
    script_path = main / "scripts" / "ensure-velites.sh"
    script_path.parent.mkdir(parents=True)
    shutil.copy(SCRIPT, script_path)
    shutil.copy(PLANNER, main / "scripts" / "velites_deploy_plan.py")
    for relative in _PLANNER_PY_IMPORTS:
        target = main / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / relative, target)
    (main / "velites").mkdir()
    (main / "velites" / "Cargo.toml").write_text(_CARGO_TOML)
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
        # 仓库 venv 的解释器跑 planner（合成 repo 无 venv；系统 python3
        # 可能 < 3.11 无 tomllib）。
        "VELITES_PLAN_PYTHON": sys.executable,
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


def test_existing_velites_sandbox_is_refreshed_in_lockstep(
    tmp_path: Path,
) -> None:
    """#835 codex P2：目录里已有 velites-sandbox 时必须与 velites 同批刷新。

    沙箱解析（shared/code_sandbox.py 的 SANDBOX_BINARY_CANDIDATES）候选序
    velites-sandbox 优先——旧包装器存在即盖住刚刷新的 velites，Host/Worker
    的 code 节点继续用旧沙箱（#831 同构漂移）。不存在时不主动创造（裸机
    默认走 velites 兜底）。"""
    main, env, log = _setup(tmp_path)
    bundled_dir = main / "data" / "bin"

    # 初装：只有 velites（历史形态，无 velites-sandbox）。
    assert _run(main, env, "--dest", "data/bin").returncode == 0
    assert (bundled_dir / "velites-sandbox").exists() is False

    # 仓库前进，运维手工放入旧 velites-sandbox（或上轮遗留）。
    Path(env["STUB_HASH_FILE"]).write_text("hash-v3\n")
    _write_stub(
        bundled_dir / "velites-sandbox",
        "#!/usr/bin/env bash\n# stale wrapper\n",
    )
    (bundled_dir / "velites-sandbox.src-stamp").write_text("hash-v1\n")

    result = _run(main, env, "--dest", "data/bin")
    assert result.returncode == 0, result.stderr
    # 旧包装器被同指纹重建替换；与 velites 共享同一 SRC_ID stamp。
    assert (bundled_dir / "velites-sandbox").read_text() == "sandbox-for-hash-v3\n"
    assert (bundled_dir / "velites-sandbox.src-stamp").read_text() == "hash-v3\n"
    assert (bundled_dir / "velites.src-stamp").read_text() == "hash-v3\n"
    # velites 本体同样刷新——两 bin 一个单元。
    assert (bundled_dir / "velites").read_text() == "binary-for-hash-v3\n"

    # 幂等：stamp 一致时不重复安置（无输出即未走替换分支）。
    result = _run(main, env, "--dest", "data/bin")
    assert result.returncode == 0, result.stderr
    assert "已安装到" not in result.stdout


def test_fresh_velites_but_stale_wrapper_still_triggers_refresh(
    tmp_path: Path,
) -> None:
    """#835 codex R4 P2（fast-path 短路）：velites 本体与 stamp 全新鲜、
    SRC_ID 也不变——本 PR 的实际首发形态——目录里却有旧 velites-sandbox。

    旧逻辑的快速路径只看 velites 本体，`exit 0` 在包装器刷新之前执行，
    刷新块一次都不会跑（首发即死代码）。planner 的判鲜是家族级的：任何
    应安置成员不新鲜即整族重建，velites 本体随之重装（幂等无害）。"""
    main, env, log = _setup(tmp_path)
    bundled_dir = main / "data" / "bin"

    # 初装（无包装器痕迹），SRC_ID 保持 hash-v1 不变——PR 不碰 velites/。
    assert _run(main, env, "--dest", "data/bin").returncode == 0
    assert (bundled_dir / "velites").read_text() == "binary-for-hash-v1\n"
    log.write_text("")

    # 存量机器手工放入旧包装器（或历史遗留），velites 本体全新鲜。
    _write_stub(
        bundled_dir / "velites-sandbox",
        "#!/usr/bin/env bash\n# stale wrapper\n",
    )
    (bundled_dir / "velites-sandbox.src-stamp").write_text("hash-v0\n")

    result = _run(main, env, "--dest", "data/bin")
    assert result.returncode == 0, result.stderr
    assert (bundled_dir / "velites-sandbox").read_text() == "sandbox-for-hash-v1\n"
    assert (bundled_dir / "velites-sandbox.src-stamp").read_text() == "hash-v1\n"
    # velites 本体被家族判鲜连带重装（同批），不是跳过。
    assert (bundled_dir / "velites").read_text() == "binary-for-hash-v1\n"


def test_stale_velites_sandbox_stamp_alone_triggers_refresh(tmp_path: Path) -> None:
    """孤儿 stamp（二进制被删、stamp 残留）同样触发 velites-sandbox 通道：
    候选序上它优先，任何残留痕迹都必须被收敛到当前指纹。"""
    main, env, log = _setup(tmp_path)
    bundled_dir = main / "data" / "bin"
    assert _run(main, env, "--dest", "data/bin").returncode == 0
    Path(env["STUB_HASH_FILE"]).write_text("hash-v2\n")
    # 初装不创造 velites-sandbox（上面断言过），这里只放一个孤儿 stamp。
    (bundled_dir / "velites-sandbox.src-stamp").write_text("hash-v1\n")

    result = _run(main, env, "--dest", "data/bin")
    assert result.returncode == 0, result.stderr
    assert (bundled_dir / "velites-sandbox").read_text() == "sandbox-for-hash-v2\n"
    assert (bundled_dir / "velites-sandbox.src-stamp").read_text() == "hash-v2\n"


def test_path_wrapper_in_different_dir_is_refreshed(
    tmp_path: Path,
) -> None:
    """#835 codex R4 P2（PATH 目录分叉）：PATH 上 velites 与 velites-sandbox
    来自不同目录时，两个位置都必须刷新。

    沙箱解析对每个候选名**独立** which（shared/code_sandbox.py 的
    resolve_sandbox_binary）——按 velites 的兄弟路径推导包装器位置会刷
    错地方：/usr/local/bin/velites-sandbox 配合 ~/.local/bin/velites 时，
    code 沙箱解析到前者，只刷后者对它不生效。planner 从真实 resolver 的
    查找结果推导安置目标，两个位置各得一个目标。"""
    main, env, log = _setup(tmp_path)
    stub_dir = Path(env["PATH"].split(":")[0])
    other_dir = tmp_path / "other"
    other_dir.mkdir()

    # PATH：velites 在 stub_dir（hash-v0 旧副本），wrapper 在 other_dir。
    _write_stub(stub_dir / "velites", "#!/usr/bin/env bash\n# old velites\n")
    (stub_dir / "velites.src-stamp").write_text("hash-v0\n")
    _write_stub(other_dir / "velites-sandbox", "#!/usr/bin/env bash\n# stale wrapper\n")
    (other_dir / "velites-sandbox.src-stamp").write_text("hash-v0\n")
    env["PATH"] = f"{other_dir}:{env['PATH']}"

    result = _run(main, env)
    assert result.returncode == 0, result.stderr
    # 两个 PATH 位置都刷新到当前指纹（wrapper 的独立 which 位置不再漏）。
    assert (stub_dir / "velites").read_text() == "binary-for-hash-v1\n"
    assert (stub_dir / "velites.src-stamp").read_text() == "hash-v1\n"
    assert (other_dir / "velites-sandbox").read_text() == "sandbox-for-hash-v1\n"
    assert (other_dir / "velites-sandbox.src-stamp").read_text() == "hash-v1\n"
    # stub_dir 不因 velites 存在而被连带安置 wrapper（不主动创造）。
    assert (stub_dir / "velites-sandbox").exists() is False


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

    # 首次安装（install-deps 通道）：data/bin 副本 = hash-v1
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


# ---- 部署面矩阵契约（#835 codex P2 的系统性收口，planner 化收编） ----
#
# velites 家族的部署面单一事实源是 scripts/velites_deploy_plan.py：它从
# 真实 resolver（shared/code_sandbox、worker/runtime/catalog）与
# velites/Cargo.toml 推导安置目标，ensure-velites.sh 逐行消费。契约把三
# 方集合钉成等式：
#
#   解析侧消费的 velites 家族 bin（沙箱候选 ∪ runtime catalog，∩
#   cargo [[bin]]）== planner 认识的家族 == 脚本安置协议覆盖的集合
#
# 单测见 tests/scripts/test_velites_deploy_plan.py；这里钉脚本侧的协议
# 接线（planner 缺席时脚本必须显式失败，不得静默退化为「只装 velites」
# 的旧模型——那正是四轮 finding 的根源形态）。


def test_script_fails_loudly_when_planner_is_broken(tmp_path: Path) -> None:
    """planner 不可用（损坏/删失/解释器缺失）时脚本必须非零退出**并打印
    捕获的诊断**，而不是回退内置安置逻辑或无提示死亡——部署面的决策面
    只有 planner 一个（codex R5 P2：set -e 下赋值继承命令替换退出码，
    曾在赋值处直接终止 shell，诊断 echo 永远执行不到，失败无提示）。"""
    main, env, log = _setup(tmp_path)
    (main / "scripts" / "velites_deploy_plan.py").unlink()
    result = _run(main, env, "--dest", "data/bin")
    assert result.returncode != 0
    # 诊断可见：捕获的 planner 报错（含其 stderr）必须到达脚本的 stderr。
    assert "velites_deploy_plan.py 执行失败" in result.stderr
    assert "No such file" in result.stderr or "can't open file" in result.stderr
    # 关键是不再产出「已安装」假象，也不静默宣称最新。
    assert "已安装到" not in result.stdout
    assert "已是最新" not in result.stdout


def test_script_refreshes_copy_that_lost_exec_bit(
    tmp_path: Path,
) -> None:
    """codex R5 P2 行为级回归：stamp 匹配但执行位丢失（无 -p 拷贝/权限
    变更）→ 判鲜必须按 resolver 的接受谓词（is_file + X_OK）判需重建，
    脚本重装后副本恢复可执行——否则脚本宣称最新而 Worker 实际跳过该
    副本、回落 PATH 旧版本或启动失败。"""
    main, env, log = _setup(tmp_path)
    bundled_dir = main / "data" / "bin"

    assert _run(main, env, "--dest", "data/bin").returncode == 0
    log.write_text("")

    # 执行位丢失，stamp 与 SRC_ID 均不变。
    binary = bundled_dir / "velites"
    binary.chmod(binary.stat().st_mode & ~stat.S_IXUSR & ~stat.S_IXGRP & ~stat.S_IXOTH)

    result = _run(main, env, "--dest", "data/bin")
    assert result.returncode == 0, result.stderr
    assert "跳过构建" not in result.stdout
    assert "已安装到" in result.stdout
    assert binary.read_text() == "binary-for-hash-v1\n"
    assert binary.stat().st_mode & stat.S_IXUSR
