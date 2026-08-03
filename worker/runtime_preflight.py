"""Worker 启动预检：声明的 runtime 所需二进制必须在 PATH 上。

声明了 velites 但 PATH 没有对应二进制时，Worker 照常注册、claim 后 spawn
即失败，任务在 Host 侧空转重试——与"注册失败"相比更难发现。注册前 fail
loudly，把部署缺口变成明确的启动错误。openclaw 不做探测：Host dispatch
对其本就 fail-fast，不存在 claim 后 spawn 的路径。
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable

# runtime -> 所需可执行文件名（shutil.which 探测）。
RUNTIME_BINARIES = {"pi": "pi", "velites": "velites"}


def missing_runtime_binaries(runtimes: Iterable[str]) -> dict[str, str]:
    """Return {runtime: binary} for declared runtimes whose binary is not on PATH."""
    missing: dict[str, str] = {}
    for runtime in sorted({str(value) for value in runtimes}):
        binary = RUNTIME_BINARIES.get(runtime)
        if binary is not None and shutil.which(binary) is None:
            missing[runtime] = binary
    return missing


def preflight_error(runtimes: Iterable[str]) -> str | None:
    """Human-readable startup error when a declared runtime lacks its binary."""
    missing = missing_runtime_binaries(runtimes)
    if not missing:
        return None
    details = "；".join(
        f"运行时 {runtime!r} 需要可执行文件 {binary!r}，但 PATH 上找不到"
        for runtime, binary in missing.items()
    )
    return (
        f"Agent Worker 启动预检失败：{details}；"
        "请安装对应二进制并确认其在 PATH 上，或从 runtimes 配置移除该运行时后重启"
    )
