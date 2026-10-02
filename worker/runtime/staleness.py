"""#831 velites 副本新鲜度对账（软告警，启动日志可见）。

preflight.py 的 file_budget 豁免条款（exemption 50）写明「preflight 出现
第三个守卫时拆分」——本模块就是那个第三守卫：把 Worker **实际解析到**的
velites 二进制（``worker/binary_resolution.py``：自带副本 data/bin 优先、
PATH 兜底）旁的 ``.src-stamp`` 指纹与仓库当前 ``velites/`` 子树的 git
tree hash 比对，不一致即产出告警文案。

背景：``native-prod-up.sh`` 历史版本只以 PATH 模式调 ``ensure-velites.sh``，
而解析顺序让首次 install-deps 安置的 ``data/bin`` 副本永久优先命中——
velites 升级（含内存硬上限等安全修复）在原生生产环境静默失效。修复后
prod-up 对两个安置点都刷新，本对账是其可见性兜底：漂移仍在（刻意维持
旧版 / 手工安置）时启动日志必须说话。

刻意不 fail-closed：velites 版本线独立于仓库（check_versions.py 的解耦
纪律），允许刻意落后；无 stamp（GitHub Release 产物/手工安置）或指纹
不可得（docker 镜像无 repo、无 git）时无从对账，静默跳过——「不可对账」
不是告警对象。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from worker.binary_resolution import resolve_binary

#: 仓库根（.git 与 velites/ 源码树所在）：裸机形态 Worker 自仓库根启动，
#: 指纹对账用它算期望指纹；docker 镜像只带 worker/ + shared/（无 repo），
#: git 探测失败即静默跳过对账。
_REPO_ROOT = Path(__file__).resolve().parents[2]

#: 源码指纹 stamp 后缀，与 scripts/ensure-velites.sh 的
#: ``STAMP="${VELITES_BIN}.src-stamp"`` 同约定。
_SRC_STAMP_SUFFIX = ".src-stamp"


def _expected_velites_fingerprint(repo_root: Path) -> str | None:
    """仓库当前 velites/ 源码指纹（git tree hash）。

    git 不可用、非 git 仓库或仓库无 velites/ 子树时返回 None（对账无从
    进行——调用方按「不可对账」跳过，不是异常）。"""

    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD:velites"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def velites_staleness_warning() -> str | None:
    """指纹对账：解析到的 velites 二进制 vs 仓库源码；漂移返回告警文案。

    把**实际解析到**的二进制 stamp 与仓库当前 velites/ 指纹比对，不一致
    即返回告警文案（漂移可见）。无 stamp、stamp 不可读、指纹不可得或
    velites 不可解析时返回 None。"""

    binary = resolve_binary("velites")
    if binary is None:
        return None
    stamp = Path(f"{binary}{_SRC_STAMP_SUFFIX}")
    try:
        stamped = stamp.read_text(encoding="utf-8").strip()
    except OSError:
        return None  # stamp 不可读与缺失同级：无从对账，不告警
    if not stamped:
        return None
    expected = _expected_velites_fingerprint(_REPO_ROOT)
    if expected is None or stamped == expected:
        return None
    return (
        f"警告: Worker 解析到的 velites 二进制 {binary} 的源码指纹"
        f"（{stamped[:12]}）与仓库 velites/ 源码指纹（{expected[:12]}）不一致——"
        "该副本将持续被 agent runtime 与 code 沙箱使用（解析顺序：自带副本优先、"
        "PATH 兜底），velites 的修复（内存上限、输出截断等）可能未生效。"
        "对齐方式: make prod-up（自动刷新 PATH 与 data/bin 两处副本）或"
        " ./scripts/ensure-velites.sh --dest data/bin；刻意维持该版本可忽略本告警"
    )
