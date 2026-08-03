"""Worker 启动预检：声明的 runtime 所需二进制必须在 PATH 上。

声明了 runtime 但 PATH 没有对应二进制时，Worker 照常注册、claim 后 spawn
即失败，任务在 Host 侧空转重试——与"注册失败"相比更难发现。注册前 fail
loudly，把部署缺口变成明确的启动错误。

探测规则（Worker 本地无法获知 Host 的 workflows.pi.flavor / 自定义
binary，按可知信息尽力判定）：velites 严格要求 velites 二进制
（runtime: velites 钉死 velites 实现）；pi 接受 pi 或 velites 任一
（flavor 二选一决定 argv[0]）；openclaw 不探测（Host dispatch 对其本就
fail-fast，不存在 claim 后 spawn 的路径）。Host 配置自定义
workflows.pi.binary 时，运维需自行保证该二进制在 PATH 上。
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable

# runtime -> 可接受的 argv[0] 候选（任一在 PATH 上即通过探测）。
RUNTIME_BINARY_CANDIDATES = {"pi": ("pi", "velites"), "velites": ("velites",)}


def missing_runtime_binaries(runtimes: Iterable[str]) -> dict[str, tuple[str, ...]]:
    """Return {runtime: candidates} for declared runtimes with no candidate on PATH."""
    missing: dict[str, tuple[str, ...]] = {}
    for runtime in sorted({str(value) for value in runtimes}):
        candidates = RUNTIME_BINARY_CANDIDATES.get(runtime)
        if candidates and not any(shutil.which(binary) for binary in candidates):
            missing[runtime] = candidates
    return missing


def preflight_error(runtimes: Iterable[str]) -> str | None:
    """Human-readable startup error when a declared runtime lacks its binary."""
    missing = missing_runtime_binaries(runtimes)
    if not missing:
        return None
    details = "；".join(
        f"运行时 {runtime!r} 需要可执行文件 {' 或 '.join(repr(b) for b in candidates)}，"
        "但 PATH 上找不到"
        for runtime, candidates in missing.items()
    )
    return (
        f"Agent Worker 启动预检失败：{details}；"
        "请安装对应二进制并确认其在 PATH 上，或从 runtimes 配置移除该运行时后重启"
    )
