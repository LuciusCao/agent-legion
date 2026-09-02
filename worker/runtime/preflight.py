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
"""

from __future__ import annotations

from shared.code_sandbox import resolve_sandbox_binary
from worker import binary_resolution
from worker.runtime.catalog import (
    RUNTIME_CATALOG,
    SUPPORTED_RUNTIMES,
    detect_installed_runtimes,
)

#: 期望 runtime 环境变量：docker 部署在 compose 里声明，裸机部署可写进
#: 服务管理器单元。值为空/未设时不启用该守卫（保持零 runtime 合法的现状）。
EXPECT_RUNTIMES_ENV = "AGENT_WORKER_EXPECT_RUNTIMES"


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
            bundled_dir = binary_resolution.BUNDLED_BINARY_DIR
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
