"""Worker 启动预检：code 执行容量所需二进制必须可解析。

issue #254 起，agent runtime 的注册声明不再是手工勾选，而是读取配置时按
二进制探测现算（``worker/runtime/catalog.py``：探测到即默认启用，
``disabled_runtimes`` 反选停用）——「声明了 runtime 但二进制缺失」这一
错误类随之结构性消除，预检不再处理 agent runtime 维度。

保留的唯一守卫：``max_code_concurrency`` > 0 时要求 velites 可解析——
所有 code 执行统一经 ``velites sandbox wrap`` 沙箱（EXEC-CODE-003，
fail-closed），与是否启用 velites *agent* runtime 无关。二进制解析
（自带副本 data/bin 优先、PATH 兜底）统一走
``worker/binary_resolution.py::resolve_binary``。
"""

from __future__ import annotations

from worker import binary_resolution
from worker.binary_resolution import resolve_binary


def preflight_error(*, code_concurrency: int = 0) -> str | None:
    """Human-readable startup error when code capacity lacks its sandbox binary."""
    if code_concurrency > 0 and resolve_binary("velites") is None:
        bundled_dir = binary_resolution.BUNDLED_BINARY_DIR
        return (
            "Agent Worker 启动预检失败：声明了 code 执行容量（max_code_concurrency > 0）"
            "需要可执行文件 'velites'（code 任务统一经 velites sandbox wrap 沙箱执行），但 "
            f"{bundled_dir} 与 PATH 上都找不到；"
            "请安装 velites（仓库内部署可执行 scripts/ensure-velites.sh --dest data/bin"
            " 构建并安置自带副本）或将 max_code_concurrency 设为 0 后重启"
        )
    return None
