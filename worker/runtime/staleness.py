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

不变量：**软告警绝不阻断启动**（codex review on #835）。漂移检查读文件
系统、跑子进程、解字节流，失败面是开放集合——点状 except 白名单封口
必有洞（非 UTF-8 stamp 的 ``UnicodeDecodeError`` 曾可穿透到
``prepare_runtime_models`` 使 Worker 以退出码 1 crash-loop）。因此
``velites_staleness_warning`` 最外层带总兜底，「不可对账」一律返回 None：
无 stamp（GitHub Release 产物/手工安置）、stamp 损坏、指纹不可得（docker
镜像无 repo、无 git）都静默跳过——刻意不 fail-closed，velites 版本线
独立于仓库（check_versions.py 的解耦纪律），允许刻意落后。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from worker.binary_resolution import resolve_binary

#: 仓库根（.git 与 velites/ 源码树所在）：裸机形态 Worker 自仓库根启动，
#: 指纹对账用它算期望指纹；docker 镜像只带 worker/ + shared/（无 repo），
#: 前置形态检查不过即静默跳过对账。
_REPO_ROOT = Path(__file__).resolve().parents[2]

#: 源码指纹 stamp 后缀，与 scripts/ensure-velites.sh 的
#: ``STAMP="${VELITES_BIN}.src-stamp"`` 同约定。
_SRC_STAMP_SUFFIX = ".src-stamp"

#: 指纹形态：git tree hash——SHA-1 仓库 40 位、SHA-256 仓库 64 位 hex，
#: 两个明确长度（41–63 位的中间长度不存在于任何 git 对象 ID，是截断/
#: 损坏的征兆）。stamp 与 git 输出都须整体匹配——垃圾内容（跨版本格式
#: 变化、手工随意写入、PATH 上 git 包装器的额外输出行）按「不可对账」
#: 处理，防止必然不等的假告警与多行内容注入启动日志。
_FINGERPRINT_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")

#: stamp 体积上限：正常 tree hash ≤ 64 字节 + 换行；超限视为损坏，不做
#: 全量读入（巨文件 OOM 防线）。
_STAMP_MAX_BYTES = 128


def _expected_velites_fingerprint(repo_root: Path) -> str | None:
    """仓库当前 velites/ 源码指纹（git tree hash）。

    前置形态检查（仓库根有 .git——worktree 的 .git 是文件——且有
    velites/ 子树）不过时不调 git：``git -C`` 会向上发现无关的祖先仓库，
    若它恰有 velites/ 子树会拿到它的 tree hash 制造假告警。git 不可用、
    非仓库、HEAD 无 velites 子树时返回 None。"""

    if not (repo_root / ".git").exists() or not (repo_root / "velites").is_dir():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD:velites"],
            capture_output=True,
            text=True,
            # errors="replace"：指纹是 hex，替换解码无损，且封死 stdout/
            # stderr 在非 UTF-8 locale 下的解码异常（ValueError 家族）。
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    fingerprint = result.stdout.strip()
    return fingerprint if _FINGERPRINT_RE.fullmatch(fingerprint) else None


def _reconcile() -> str | None:
    """对账本体（不含总兜底）：漂移返回告警文案，不可对账返回 None。"""

    binary = resolve_binary("velites")
    if binary is None:
        return None
    stamp = Path(f"{binary}{_SRC_STAMP_SUFFIX}")
    # is_file() 对 FIFO/目录返回 False：命名管道会挂死 open()（比崩溃更难
    # 诊断的启动挂起），目录会抛 IsADirectoryError——都按不可对账处理。
    if not stamp.is_file():
        return None
    try:
        if stamp.stat().st_size > _STAMP_MAX_BYTES:
            return None
        stamped = stamp.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None  # 读失败/解码失败与缺失同级：无从对账，不告警
    if not stamped or not _FINGERPRINT_RE.fullmatch(stamped):
        return None
    expected = _expected_velites_fingerprint(_REPO_ROOT)
    if expected is None or stamped == expected:
        return None
    # 方向中性：tree hash 无时序，PATH 副本又跨 worktree 共享——副本可能
    # 落后于本仓库（修复未生效）也可能领先（来自其他 worktree 的更新
    # 构建），两种解读都给出，操作者才不会被反向误导。
    return (
        f"警告: Worker 解析到的 velites 二进制 {binary} 的源码指纹"
        f"（{stamped[:12]}）与仓库 velites/ 源码指纹（{expected[:12]}）不一致——"
        "该副本将持续被 agent runtime 与 code 沙箱使用（解析顺序：自带副本优先、"
        "PATH 兜底）。可能是副本落后于源码（velites 的修复如内存上限、输出截断"
        "未生效），也可能是 PATH 副本领先于本仓库版本线（PATH 上的 velites 跨"
        "worktree 共享）。对齐方式: make prod-up（自动刷新 PATH 与 data/bin 两处"
        "副本）或 ./scripts/ensure-velites.sh --dest data/bin；刻意维持该版本"
        "可忽略本告警"
    )


def velites_staleness_warning() -> str | None:
    """指纹对账（软告警）：解析到的 velites 二进制 vs 仓库源码。

    漂移返回告警文案；不可对账或对账过程任何失败返回 None——见模块
    docstring 的不变量。"""

    try:
        return _reconcile()
    except Exception:  # noqa: BLE001
        # #204 broad-except audit: 软告警的总兜底，「漂移可见」绝不演变为
        # 「阻断启动」——读取点/子进程/解码的失败面是开放集合，白名单封口
        # 必有洞（UnicodeDecodeError 穿透曾使 Worker crash-loop）。已知族
        # 已在 _reconcile/_expected 内细分降级，到达这里的是未枚举形态，
        # 吞掉即「不可对账」语义，无重试价值也无需留痕（下次启动自然再试）。
        return None
