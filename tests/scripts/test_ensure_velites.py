"""Contract tests for scripts/ensure-velites.sh staleness detection.

The script resolves ROOT from its own location, so tests copy it into a
synthetic repo layout and run it with stubbed ``git``/``cargo`` on a
restricted PATH: no real repo, cargo build, or PATH velites is touched.
"""

from __future__ import annotations

import re
import shutil
import stat
import subprocess
import tomllib
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

# cargo runs with cwd=<root>/velites (the script cd's there before building);
# 同批产出沙箱包装器（ensure-velites.sh 的 velites-sandbox 同步安置依赖它）。
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
    assert "沙箱包装器" not in result.stdout


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


# ---- 部署面矩阵契约（#835 codex P2 的系统性收口） ----
#
# velites 家族的部署面没有单一事实源：2 个解析器（worker/binary_resolution
# 的 runtime 面、shared/code_sandbox 的沙箱面）× 2 个安置位置（PATH /
# data/bin）× N 个 bin 组合，每个部署脚本各自想全——漏一个就是一轮静默
# 滞留（#831 的 velites 本体、#835 的 velites-sandbox 同构）。契约把三方
# 集合成等式钉死：
#
#   解析侧消费的 velites 家族 bin（沙箱候选 ∪ runtime catalog，与
#   velites/Cargo.toml [[bin]] 清单的交集）== ensure-velites.sh 实际
#   安置的 bin（脚本文本中的 velites/target/release/<name> 引用）
#
# 新增 [[bin]] 且有运行时消费者（或反向：解析侧开始消费一个新名字）而
# 脚本未同步安置时，等式破裂直接红。pi 等外部 runtime 不在 cargo 清单，
# 天然排除；velites-schema 无运行时消费者，不在等式要求内（不强求安置，
# 但被安置也合法——等式只约束「消费 ⊆ 安置」+「安置 ⊆ 构建」）。


def _velites_deploy_matrix() -> tuple[set[str], set[str], set[str]]:
    """返回 (cargo 声明的 bin, 解析侧消费的 velites 家族 bin, 脚本安置的 bin)。"""

    cargo_bins = {
        entry["name"]
        for entry in tomllib.loads((ROOT / "velites" / "Cargo.toml").read_text(encoding="utf-8"))[
            "bin"
        ]
    }
    from shared.code_sandbox import SANDBOX_BINARY_CANDIDATES
    from worker.runtime.catalog import RUNTIME_CATALOG

    consumers = set(SANDBOX_BINARY_CANDIDATES) | {
        binary for meta in RUNTIME_CATALOG.values() for binary in meta["binaries"]
    }
    consumed_velites_bins = consumers & cargo_bins
    script = SCRIPT.read_text(encoding="utf-8")
    installed = set(re.findall(r"velites/target/release/([A-Za-z0-9_-]+)", script))
    return cargo_bins, consumed_velites_bins, installed


def test_deploy_matrix_covers_every_consumed_cargo_bin() -> None:
    """消费 ⊆ 安置：解析侧消费的每个 velites 家族 bin 都必须被
    ensure-velites.sh 安置——漏掉的 bin 在解析候选序上盖住刷新的新版本，
    是 #831/#835 同构的静默滞留。"""
    _, consumed, installed = _velites_deploy_matrix()
    missing = consumed - installed
    assert not missing, (
        f"ensure-velites.sh 未安置被解析侧消费的 velites 家族 bin: {sorted(missing)}"
        "——worker/binary_resolution.py 与 shared/code_sandbox.py 会解析到旧副本，"
        "升级静默失效（在脚本中为其补「产物安置 + src-stamp」通道）"
    )


def test_deploy_matrix_only_installs_cargo_declared_bins() -> None:
    """安置 ⊆ 构建：脚本安置的每个 bin 必须真实存在于 velites/Cargo.toml
    的 [[bin]] 清单——拼错名字或安置已删除的 bin 会让安置分支静默失败
    （cp 源不存在），stamp 却照写，制造「已刷新」假象。"""
    cargo_bins, _, installed = _velites_deploy_matrix()
    ghost = installed - cargo_bins
    assert not ghost, (
        f"ensure-velites.sh 安置了 velites/Cargo.toml 未声明的 bin: {sorted(ghost)}"
        "——cargo 构建不产出该文件，安置分支必然失败（核对手误或过时的 bin 名）"
    )
