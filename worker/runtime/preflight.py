"""Worker 启动预检：期望 runtime 与 code 执行容量所需二进制必须可解析。

issue #254 起，agent runtime 的注册声明不再是手工勾选，而是读取配置时按
二进制探测现算（``worker/runtime/catalog.py``：探测到即默认启用，
``disabled_runtimes`` 反选停用）——「声明了 runtime 但二进制缺失」这一
错误类随之结构性消除。

两个守卫（#381 起 velites 等执行器移出 worker 镜像、改为外挂二进制，
「忘记挂载」从部署事故变成高频人误，静默零容量不可接受）：

1. ``AGENT_WORKER_EXPECT_RUNTIMES``（逗号分隔，如 ``velites`` 或
   ``velites,pi``）：部署方声明本机必须具备的 runtime，探测不到任何一个
   即 fail-fast。专治「docker worker 忘了挂载 velites」——该形态下自动
   探测得到空集、worker 照常注册但零容量，Host 侧只能看到任务没人领。
2. ``max_code_concurrency`` > 0 时要求 velites 可解析——所有 code 执行
   统一经 ``velites sandbox wrap`` 沙箱（EXEC-CODE-003，fail-closed），
   与是否启用 velites *agent* runtime 无关。

二进制解析（自带副本 data/bin 优先、PATH 兜底）统一走
``worker/binary_resolution.py::resolve_binary``。期望值必须是
``worker/runtime/catalog.py`` 的 SUPPORTED_RUNTIMES 子集，未知值同样
fail-fast（拼写错误按部署错误处理，不静默忽略）。

#831 补一道软对账（``velites_staleness_warning``）：把实际解析到的
velites 副本的源码 stamp 与仓库当前 velites/ 指纹比对，漂移只告警不
fail-closed——修复「PATH 刷了、自带副本没刷」这一静默滞留形态的可见性。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from shared import code_sandbox
from shared.code_sandbox import resolve_sandbox_binary
from worker.binary_resolution import resolve_binary
from worker.runtime.catalog import (
    RUNTIME_CATALOG,
    SUPPORTED_RUNTIMES,
    detect_installed_runtimes,
)

#: 期望 runtime 环境变量：docker 部署在 compose 里声明，裸机部署可写进
#: 服务管理器单元。值为空/未设时不启用该守卫（保持零 runtime 合法的现状）。
EXPECT_RUNTIMES_ENV = "AGENT_WORKER_EXPECT_RUNTIMES"

#: 仓库根（.git 与 velites/ 源码树所在）：裸机形态 Worker 自仓库根启动，
#: #831 指纹对账用它算期望指纹；docker 镜像只带 worker/ + shared/（无
#: repo），git 探测失败即静默跳过对账。
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
    """#831 指纹对账（软告警）：解析到的 velites 二进制 vs 仓库源码。

    Worker/Host 的二进制解析是「自带副本 data/bin 优先、PATH 兜底」——
    两处安置点都由 ensure-velites.sh 按源码指纹刷新并留 stamp；本函数把
    **实际解析到**的二进制 stamp 与仓库当前 velites/ 指纹比对，不一致即
    返回告警文案（漂移可见）。刻意不 fail-closed：velites 版本线独立于
    仓库，允许刻意落后，但静默漂移（PATH 刷了、自带副本没刷）必须可见。
    无 stamp（Release 产物/手工安置）或指纹不可得（无 git/无源码树）时
    返回 None——无从对账不是告警对象。"""

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


def parse_expect_runtimes(raw: str | None) -> list[str] | None:
    """解析环境变量值为期望 runtime 列表；None = 守卫未启用。

    空/仅空白 = 未启用；逗号分隔的每个条目必须落在 SUPPORTED_RUNTIMES，
    未知值抛 ValueError（拼写错误按部署错误处理）；重复值去重（错误文案
    逐项点名，重复会在文案里复读）。"""

    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    items: list[str] = []
    for item in (part.strip() for part in value.split(",")):
        if item and item not in items:
            items.append(item)
    unknown = [item for item in items if item not in SUPPORTED_RUNTIMES]
    if unknown:
        raise ValueError(
            f"{EXPECT_RUNTIMES_ENV} 含不支持的 runtime：{'、'.join(unknown)}"
            f"（受支持的值：{', '.join(SUPPORTED_RUNTIMES)}）"
        )
    return items


def preflight_error(
    *, code_concurrency: int = 0, expect_runtimes: list[str] | None = None
) -> str | None:
    """Human-readable startup error for failed runtime/capacity preconditions."""
    if expect_runtimes:
        installed = detect_installed_runtimes()
        missing = [runtime for runtime in expect_runtimes if runtime not in installed]
        if missing:
            bundled_dir = code_sandbox.BUNDLED_SANDBOX_DIR
            hints = "；".join(
                f"{runtime!r} 需要可执行文件 {'/'.join(RUNTIME_CATALOG[runtime]['binaries'])}"
                for runtime in missing
            )
            return (
                f"Agent Worker 启动预检失败：{EXPECT_RUNTIMES_ENV} 声明了期望的 agent"
                f" runtime（{', '.join(expect_runtimes)}），但本机探测不到：{hints}。"
                f"自查方向：二进制是否挂载/安装到 {bundled_dir} 或 PATH、是否可执行；"
                "注意存在性探测通过不代表可执行——架构错配在模型发现阶段才暴露"
                "（发现失败同样 fail-fast，见 agent-worker-deployment.md §5）；"
                "确认本机确实不需要该 runtime 时，从环境变量中移除后重启"
            )
    if code_concurrency > 0 and resolve_sandbox_binary() is None:
        return (
            "Agent Worker 启动预检失败：声明了 code 执行容量（max_code_concurrency > 0）"
            "需要沙箱包装器（velites-sandbox 或 velites 任一在 PATH 上，code 任务统一经"
            " velites sandbox wrap 沙箱执行，EXEC-CODE-003），但都找不到；"
            "docker 形态的沙箱包装器已内置在镜像里（velites-sandbox），此错误通常意味着"
            "镜像损坏；裸机形态可执行 scripts/ensure-velites.sh 构建 velites 或将"
            " max_code_concurrency 设为 0 后重启"
        )
    return None
