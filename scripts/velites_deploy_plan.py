"""velites 部署面 planner：从**真实 resolver** 推导安置/判鲜目标。

#835 的四轮 codex 评审暴露了同一个根源：``ensure-velites.sh``（bash）对
「哪些 bin、安置在哪、什么算新鲜」持有自己的一份平行模型，靠人肉记忆与
Python 侧 resolver（``worker/binary_resolution.py`` / ``shared/code_sandbox.py``）
保持同步——bin 集合、候选优先序、PATH 独立查找、freshness 单元，每个维
度失同步就是一轮静默滞留（#831 漏 data/bin 通道；#835 fast-path 短路、
PATH 目录分叉、漏刷 velites-sandbox）。本模块把决策面挪到 Python、紧挨
resolver：脚本的查找逻辑从此**问** resolver，而不是猜。

事实来源（同一进程 import，零拷贝）：

- bin 集合：``shared/code_sandbox.SANDBOX_BINARY_CANDIDATES``（沙箱面）
  ∪ ``worker.runtime.catalog.RUNTIME_CATALOG`` 的 binaries（runtime 面），
  ∩ ``velites/Cargo.toml`` 的 ``[[bin]]``（构建面真实产物；pi 等外部
  runtime 不在 cargo 清单，天然排除；velites-schema 无运行时消费者，
  同样不在集合内）。
- 安置/对账位置：``shared.code_sandbox.sandbox_resolution_walk()``——与
  运行时解析**同一份**目录语义与 PATH 查找（#835 codex P2：PATH 上
  ``velites`` 与 ``velites-sandbox`` 可来自不同目录，按 velites 的兄弟
  路径推导 wrapper 位置是错的，必须按名独立 which）。
- freshness 单元：整个 velites 构建产物是**一个指纹单元**（家族成员共享
  同一 src-stamp）——resolver 消费的是候选集合整体，按单工件判鲜就是
  #835 fast-path 短路的形状。判鲜**谓词**同样来自 resolver：接受谓词
  ``shared.code_sandbox.is_consumable_binary``（is_file + X_OK），
  执行位丢失的副本按需重建——谓词弱于 resolver 会让脚本宣称最新而
  Worker 实际跳过该副本（#835 codex R5 P2）。

通道语义（两通道各管一段，prod-up 先后都跑）：

- ``--dest DIR``（自带副本通道）：目标 = DIR 内的 velites + DIR 内已有
  存在痕迹的家族成员（bin 或孤儿 stamp；不主动创造——裸机默认走 velites
  兜底，docker 镜像的 velites-sandbox 在 /usr/local/bin，不经本脚本）。
- PATH 模式（默认）：主目标 = ``which velites`` 所在目录（保持旧脚本的
  就地刷新语义；无 PATH 副本时落 ``VELITES_INSTALL_DIR`` 或
  ``~/.local/bin``），外加 walk 在 PATH 上按名独立发现的家族成员位置
  （它们盖住同批安置的 velites，漏刷即 #835 的 PATH 分叉形态）。

CLI 行协议（``ensure-velites.sh`` 逐行消费；JSON 对 shell 是 jq/python
二次依赖）::

    plan [--dest DIR] [--src-id ID]    # 规划：每行「<bin>|<目标绝对路径>」，
                                       # 同一 bin 可多行（多个安置位置）
    check --src-id ID [--dest DIR]     # 判鲜：全新鲜时无输出；否则每行
                                       # 「refresh|<bin>」——家族级语义：任一
                                       # 目标位置不新鲜即整族重建（重构建必然
                                       # 同批产出全部家族 bin）
    candidates                         # 对账参考：沙箱面按解析序可达的全部
                                       # 位置，每行「<候选名>|<位置>」（空
                                       # 位置 = 该步缺失），供 staleness
                                       # 与文档侧对齐消费

环境说明：全部真实调用方（install-deps / native-prod-up / e2e / CI）都在
仓库侧有 python 的环境跑本入口；Docker 镜像不调本脚本（velites-sandbox
在构建期 COPY，velites 由 compose 挂载），无镜像内 python 约束。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

#: 仓库根：脚本直跑时把仓库根插进 sys.path（shared/ worker/ 可见）；作为
#: ``scripts.velites_deploy_plan`` 被 import 时该插入幂等无害。
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

#: 家族 stamp 后缀：单一事实源在 shared/velites_staleness.py（对账核心），
#: 与 scripts/ensure-velites.sh 的 ``STAMP="${VELITES_BIN}.src-stamp"`` 同
#: 约定——跨进程协议常量，bash 侧的一致性由
#: tests/scripts/test_velites_deploy_plan.py 钉住。
from shared.velites_staleness import (  # noqa: E402  # sys.path 先行（见上）
    SRC_STAMP_SUFFIX,
    read_src_stamp,
)


def _resolve_deployment_targets(dest_dir: str | None) -> list[tuple[str, str]]:
    """安置目标 (bin, 绝对路径)：决策全部来自真实 resolver 的查找结果。"""
    from shared.code_sandbox import SANDBOX_BINARY_CANDIDATES
    from worker.runtime.catalog import RUNTIME_CATALOG

    cargo_bins = _cargo_bin_names()
    family = sorted(
        (
            set(SANDBOX_BINARY_CANDIDATES)
            | {str(b) for m in RUNTIME_CATALOG.values() for b in m["binaries"]}
        )
        & cargo_bins
    )
    if "velites" not in family:
        raise SystemExit(
            f"velites_deploy_plan: 部署面契约破裂——velites 主 bin 不在消费集合（{family}）"
        )

    targets: list[tuple[str, str]] = []
    if dest_dir is not None:
        directory = Path(dest_dir)
        if not directory.is_absolute():
            directory = Path.cwd() / directory
        for member in family:
            binary_path = directory / member
            stamp_path = Path(f"{binary_path}{SRC_STAMP_SUFFIX}")
            # 候选序 velites-sandbox 优先：目录里存在它的任何痕迹（bin 本体
            # 或孤儿 stamp）都会盖住同批的 velites——存在即必须纳入刷新
            # （不主动创造，见模块 docstring 的通道语义）。
            if member == "velites" or binary_path.exists() or stamp_path.exists():
                targets.append((member, str(binary_path)))
        return targets

    # PATH 模式：主目标跟随 which velites 的现有位置（就地刷新，旧脚本
    # 语义）；无 PATH 副本时落默认安装目录。which 只查 PATH——自带副本
    # 目录（data/bin）归 --dest 通道管，这里天然不碰。
    path_velites = shutil.which("velites")
    primary_dir = Path(path_velites).parent if path_velites else Path(_default_install_dir())
    for member in family:
        binary_path = primary_dir / member
        stamp_path = Path(f"{binary_path}{SRC_STAMP_SUFFIX}")
        if member == "velites" or binary_path.exists() or stamp_path.exists():
            targets.append((member, str(binary_path)))
    # PATH 上按名独立发现的家族成员位置（#835 codex P2：PATH 上 velites
    # 与 velites-sandbox 可来自不同目录——按 velites 的兄弟路径推导是错的，
    # 必须按名独立 which）。它们盖住同批安置的 velites，漏刷即静默滞留。
    seen = {path for _, path in targets}
    for member in family:
        hit = shutil.which(member)
        if hit and hit not in seen:
            seen.add(hit)
            targets.append((member, hit))
    return targets


def _cargo_bin_names() -> set[str]:
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - CI 与开发环境均 >= 3.11
        raise SystemExit("velites_deploy_plan: 需要 Python 3.11+（tomllib）") from None
    document = tomllib.loads((_REPO_ROOT / "velites" / "Cargo.toml").read_text(encoding="utf-8"))
    return {str(entry["name"]) for entry in document.get("bin", [])}


def _default_install_dir() -> str:
    return os.environ.get("VELITES_INSTALL_DIR") or os.path.expanduser("~/.local/bin")


def _plan(dest_dir: str | None) -> list[tuple[str, str]]:
    return _resolve_deployment_targets(dest_dir)


def _check(src_id: str, dest_dir: str | None) -> list[str]:
    """家族级判鲜：任一目标位置的 bin 缺失、不可执行或 stamp 不符即整族重建。

    判鲜谓词与 resolver 的接受谓词同口径（is_consumable_binary：
    is_file + X_OK）——执行位丢失（无 -p 拷贝/权限变更）的副本
    resolver 会跳过，按「存在」判鲜会让脚本宣称最新而 Worker 回落旧
    副本或启动失败（#835 codex R5 P2）。stamp 缺失/损坏/不匹配统一按
    「不可判鲜 → 重建」处理（与主脚本对 Release 产物的既有语义一致）。
    stamp 读取复用对账核心的有界读取 read_src_stamp（常规文件 + 体积上限 +
    指纹形态校验）——FIFO/设备文件 stamp 不会挂死判鲜（#835 codex R7 P2）。"""
    from shared.code_sandbox import is_consumable_binary

    stale: set[str] = set()
    for member, path in _resolve_deployment_targets(dest_dir):
        binary_path = Path(path)
        if not is_consumable_binary(binary_path) or read_src_stamp(binary_path) != src_id:
            stale.add(member)
    return sorted(stale)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="velites_deploy_plan", description="从真实 resolver 推导 velites 安置/判鲜目标"
    )
    parser.add_argument("command", choices=("plan", "check", "candidates"))
    parser.add_argument("--dest", default=None, help="目标目录（默认 PATH 模式）")
    parser.add_argument("--src-id", default=None, help="当前 velites/ 源码指纹（git tree hash）")
    args = parser.parse_args(argv)

    if args.command == "candidates":
        from shared.code_sandbox import sandbox_resolution_walk

        for name, location in sandbox_resolution_walk():
            print(f"{name}|{location}")
        return 0
    if args.command == "check":
        if not args.src_id:
            raise SystemExit("velites_deploy_plan: check 需要 --src-id")
        for member in _check(args.src_id, args.dest):
            print(f"refresh|{member}")
        return 0
    for member, path in _plan(args.dest):
        print(f"{member}|{path}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
