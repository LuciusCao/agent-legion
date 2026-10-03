"""velites 部署面 planner（scripts/velites_deploy_plan.py）的单元与契约测试。

#835 的四轮 codex 评审根源是 ensure-velites.sh 持有与 Python resolver 平行
的查找模型；planner 把决策面挪回 resolver 侧。这里钉住三件事：

1. **协议常量**：stamp 后缀与 bash 脚本、对账核心一致（跨进程协议）。
2. **部署面矩阵契约**：解析侧消费的 velites 家族 bin ==
   planner 认识的家族 == cargo 真实产物（消费 ⊆ 安置 ⊆ 构建三向等式）。
3. **决策语义**：plan/check 在关键布局下的目标推导——PATH 模式跟随
   which velites 就地刷新 + 独立 which 家族成员（目录分叉形态）、
   --dest 模式的家族成员「存在痕迹才纳入」、无 PATH 副本时落默认安装目录。

行为级端到端（stub git/cargo 跑完整脚本）见 tests/scripts/test_ensure_velites.py。
"""

from __future__ import annotations

import os
import stat
import tomllib
from pathlib import Path

import pytest

from scripts import velites_deploy_plan as planner
from shared import code_sandbox

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ensure-velites.sh"


def _write_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _write_stamp(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{value}\n", encoding="utf-8")


# ---- 协议常量：planner ↔ bash 脚本 ↔ 对账核心 三方一致 ----


def test_stamp_suffix_matches_shell_script_and_staleness_core() -> None:
    """stamp 后缀是跨进程协议（bash 安置写 stamp、shared 对账核心读 stamp），
    planner 从 shared 单一事实源 import——三方漂移会让判鲜与对账各说各话。"""
    from shared.velites_staleness import SRC_STAMP_SUFFIX as core_suffix

    assert planner.SRC_STAMP_SUFFIX == core_suffix == ".src-stamp"
    # bash 侧的 stamp 写入仍用同一字面量（安置协议），名字出现在脚本里。
    assert '.src-stamp"' in SCRIPT.read_text(encoding="utf-8")


# ---- 部署面矩阵契约：消费 == 安置 == 构建 ----


def _velites_deploy_matrix() -> tuple[set[str], set[str]]:
    """返回 (cargo 声明的 bin, planner 认识的家族成员)。"""
    cargo_bins = {
        str(entry["name"])
        for entry in tomllib.loads((ROOT / "velites" / "Cargo.toml").read_text(encoding="utf-8"))[
            "bin"
        ]
    }
    from worker.runtime.catalog import RUNTIME_CATALOG

    consumers = set(code_sandbox.SANDBOX_BINARY_CANDIDATES) | {
        str(binary) for meta in RUNTIME_CATALOG.values() for binary in meta["binaries"]
    }
    return cargo_bins, consumers & cargo_bins


def test_planner_family_equals_consumed_cargo_bins() -> None:
    """消费 ⊆ 安置：planner 的家族成员必须覆盖解析侧消费的每个 velites 家族
    bin——漏掉的 bin 在解析候选序上盖住刷新的新版本，是 #831/#835 同构的
    静默滞留（成员判定在 _resolve_deployment_targets 的 family 计算里）。"""
    cargo_bins, consumed = _velites_deploy_matrix()
    assert "velites" in consumed  # 主 bin 必须在消费集合（否则部署面契约破裂）
    # planner 的家族推导与矩阵同源（同 import 面）；这里钉的是 import 面本身
    # 的两个事实源不被静默改动：沙箱候选与 runtime catalog。
    assert consumed == {"velites", "velites-sandbox"}
    assert cargo_bins >= consumed


def test_planner_targets_follow_resolver_not_sibling_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """PATH 目录分叉（codex R4 P2）：PATH 上 velites 与 velites-sandbox 来自
    不同目录时，两个位置都必须是安置目标——按 velites 的兄弟路径推导
    wrapper 位置是错的（resolve_sandbox_binary 对每个名字独立 which）。"""
    dir_a, dir_b = tmp_path / "a", tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    _write_executable(dir_a / "velites")
    _write_stamp(dir_a / "velites.src-stamp", "0000000000000000000000000000000000000000")
    _write_executable(dir_b / "velites-sandbox")
    _write_stamp(dir_b / "velites-sandbox.src-stamp", "0000000000000000000000000000000000000000")
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", tmp_path / "no-bundled")

    def fake_which(binary: str) -> str | None:
        return {
            name: str(directory / name)
            for directory, names in ((dir_a, ("velites",)), (dir_b, ("velites-sandbox",)))
            for name in names
        }.get(binary)

    monkeypatch.setattr("shutil.which", fake_which)

    targets = dict(planner._resolve_deployment_targets(None))

    assert targets["velites"] == str(dir_a / "velites")
    assert targets["velites-sandbox"] == str(dir_b / "velites-sandbox")


def test_path_mode_without_path_velites_falls_back_to_install_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """PATH 上没有 velites 时，主目标落 VELITES_INSTALL_DIR（与旧脚本语义
    一致——首次安装通道）。"""
    install_dir = tmp_path / "install"
    monkeypatch.setenv("VELITES_INSTALL_DIR", str(install_dir))
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", tmp_path / "no-bundled")
    monkeypatch.setattr("shutil.which", lambda _binary: None)

    targets = dict(planner._resolve_deployment_targets(None))

    assert targets["velites"] == str(install_dir / "velites")
    assert "velites-sandbox" not in targets  # 无存在痕迹，不主动创造


def test_dest_mode_includes_family_member_only_when_trace_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--dest 通道：家族成员（velites-sandbox）只在目录里有存在痕迹（bin
    本体或孤儿 stamp）时纳入目标——候选序它优先，任何痕迹都会盖住同批
    velites（#835 codex P2）；不存在则不主动创造（裸机默认走 velites 兜底）。"""
    dest = tmp_path / "data" / "bin"
    dest.mkdir(parents=True)
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", tmp_path / "no-bundled")

    # 无痕迹：只有 velites。
    targets = dict(planner._resolve_deployment_targets(str(dest)))
    assert set(targets) == {"velites"}

    # 孤儿 stamp（二进制被删、stamp 残留）同样触发。
    _write_stamp(dest / "velites-sandbox.src-stamp", "0000000000000000000000000000000000000000")
    targets = dict(planner._resolve_deployment_targets(str(dest)))
    assert set(targets) == {"velites", "velites-sandbox"}


def test_check_reports_every_stale_member(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """家族级判鲜：velites 本体全新鲜、同目录 wrapper 陈旧（codex R4 P2 的
    fast-path 短路形态）→ check 必须点名 wrapper，不能因主 bin 新鲜而放行。"""
    dest = tmp_path / "data" / "bin"
    dest.mkdir(parents=True)
    _write_executable(dest / "velites")
    _write_stamp(dest / "velites.src-stamp", "1111111111111111111111111111111111111111")
    _write_executable(dest / "velites-sandbox")
    _write_stamp(dest / "velites-sandbox.src-stamp", "0000000000000000000000000000000000000000")
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", tmp_path / "no-bundled")

    assert planner._check("1111111111111111111111111111111111111111", str(dest)) == [
        "velites-sandbox"
    ]

    # 全新鲜 → 空。
    _write_stamp(dest / "velites-sandbox.src-stamp", "1111111111111111111111111111111111111111")
    assert planner._check("1111111111111111111111111111111111111111", str(dest)) == []

    # 无 stamp（Release 产物）→ 不可判鲜 → 重建。
    (dest / "velites-sandbox.src-stamp").unlink()
    assert planner._check("1111111111111111111111111111111111111111", str(dest)) == [
        "velites-sandbox"
    ]


def test_check_flags_non_executable_copy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """codex R5 P2：判鲜谓词必须与 resolver 的接受谓词同口径（is_file +
    X_OK）——执行位丢失（无 -p 拷贝/权限变更）+ stamp 匹配的副本会被
    resolver 跳过，按「存在」判鲜会让脚本宣称最新而 Worker 回落旧副本
    或启动失败。谓词单一事实源：shared.code_sandbox.is_consumable_binary。"""
    dest = tmp_path / "data" / "bin"
    dest.mkdir(parents=True)
    binary = dest / "velites"
    _write_executable(binary)
    _write_stamp(dest / "velites.src-stamp", "1111111111111111111111111111111111111111")
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", tmp_path / "no-bundled")

    assert planner._check("1111111111111111111111111111111111111111", str(dest)) == []

    binary.chmod(binary.stat().st_mode & ~stat.S_IXUSR & ~stat.S_IXGRP & ~stat.S_IXOTH)
    assert planner._check("1111111111111111111111111111111111111111", str(dest)) == ["velites"]


def test_check_does_not_block_on_fifo_stamp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """codex R7 P2：stamp 是 FIFO 时裸 read_text() 会永久阻塞，prod-up 卡在
    判鲜。planner 复用对账核心的有界读取（read_src_stamp：常规文件 + 体积
    上限 + 指纹形态），FIFO 按「不可判鲜 → 重建」处理。"""
    dest = tmp_path / "data" / "bin"
    dest.mkdir(parents=True)
    _write_executable(dest / "velites")
    os.mkfifo(dest / "velites.src-stamp")
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", tmp_path / "no-bundled")

    assert planner._check("1" * 40, str(dest)) == ["velites"]


def test_is_consumable_binary_is_the_shared_acceptance_predicate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """接受谓词单一事实源钉子：resolve_binary 与解析 walk 都经由
    is_consumable_binary 判定自带副本——绕开它私有实现 is_file/X_OK 会
    重新引入「两份模型」（判鲜侧改谓词、解析侧不动，#835 的病根）。"""
    import shutil as shutil_module

    from shared.code_sandbox import BUNDLED_SANDBOX_DIR, is_consumable_binary
    from worker import binary_resolution

    bundled_dir = tmp_path / "bundle"
    bundled_dir.mkdir()
    binary = bundled_dir / "velites"
    _write_executable(binary)
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", bundled_dir)
    monkeypatch.setattr(
        shutil_module,
        "which",
        lambda _name: "/nonexistent/velites",  # PATH 兜底不命中
    )

    calls: list[Path] = []
    real = code_sandbox.is_consumable_binary

    def counting(path: Path) -> bool:
        calls.append(path)
        return real(path)

    monkeypatch.setattr(code_sandbox, "is_consumable_binary", counting)

    # PATH 兜底不命中时，bundled 副本经谓词接受。
    assert binary_resolution.resolve_binary("velites") == str(binary)
    from shared.code_sandbox import resolve_sandbox_binary, sandbox_resolution_walk

    resolve_sandbox_binary()
    walk = sandbox_resolution_walk()
    assert calls, "resolve_binary/walk 必须经由 is_consumable_binary"
    # bundled 步的判定来自同一谓词。
    assert (str(binary) in [hit for _, hit in walk]) == is_consumable_binary(binary)
    # 执行位丢失 → bundled 步不再命中，resolver 回落 PATH（期望行为：
    # 谓词是「接受」的单一事实源，不是解析的短路开关）。
    binary.chmod(binary.stat().st_mode & ~stat.S_IXUSR & ~stat.S_IXGRP & ~stat.S_IXOTH)
    assert binary_resolution.resolve_binary("velites") == "/nonexistent/velites"
    assert str(binary) not in [hit for _, hit in sandbox_resolution_walk()]
    assert bundled_dir != BUNDLED_SANDBOX_DIR  # 值导入 re-export 不随 patch 走（#496 已钉）


def test_plan_contract_main_bin_always_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """主 bin 永远在安置目标里（空目录的全新安装形态）。"""
    dest = tmp_path / "data" / "bin"
    dest.mkdir(parents=True)
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", tmp_path / "no-bundled")

    targets = dict(planner._resolve_deployment_targets(str(dest)))
    assert targets["velites"] == str(dest / "velites")
