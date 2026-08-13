"""Worker 启动预检：声明的 runtime 所需二进制必须在 PATH 上。

声明了 runtime 但 PATH 没有对应二进制时，Worker 照常注册、claim 后 spawn
即失败，任务在 Host 侧空转重试——与"注册失败"相比更难发现。注册前 fail
loudly，把部署缺口变成明确的启动错误。

探测规则：runtime 钉死命令构建器与二进制（agent 配置治理 phase 3 起，
manifest 的 execution.binary 由 Agent 定义的 runtime 决定：pi → pi、
velites → velites），Worker 本地按同一映射要求对应二进制在 PATH 上；
openclaw 不探测（Host dispatch 对其本就 fail-fast，不存在 claim 后
spawn 的路径）。
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable

# runtime -> 可接受的 argv[0] 候选（任一在 PATH 上即通过探测）。
RUNTIME_BINARY_CANDIDATES = {"pi": ("pi",), "velites": ("velites",)}


def missing_runtime_binaries(runtimes: Iterable[str]) -> dict[str, tuple[str, ...]]:
    """Return {runtime: candidates} for declared runtimes with no candidate on PATH."""
    missing: dict[str, tuple[str, ...]] = {}
    for runtime in sorted({str(value) for value in runtimes}):
        candidates = RUNTIME_BINARY_CANDIDATES.get(runtime)
        if candidates and not any(shutil.which(binary) for binary in candidates):
            missing[runtime] = candidates
    return missing


def preflight_error(runtimes: Iterable[str], *, code_concurrency: int = 0) -> str | None:
    """Human-readable startup error when a declared runtime lacks its binary.

    ``code_concurrency`` > 0 additionally requires velites: every code
    execution runs through ``velites sandbox wrap`` (EXEC-CODE-003,
    fail-closed), even when the Worker declares no velites *agent* runtime."""
    missing = missing_runtime_binaries(runtimes)
    reasons = [
        f"运行时 {runtime!r} 需要可执行文件 {' 或 '.join(repr(b) for b in candidates)}，"
        "但 PATH 上找不到；请安装对应二进制并确认其在 PATH 上，"
        "或从 runtimes 配置移除该运行时后重启"
        for runtime, candidates in missing.items()
    ]
    if (
        code_concurrency > 0
        and "velites" not in missing
        and not any(shutil.which(binary) for binary in RUNTIME_BINARY_CANDIDATES["velites"])
    ):
        reasons.append(
            "声明了 code 执行容量（max_code_concurrency > 0）需要可执行文件 'velites'"
            "（code 任务统一经 velites sandbox wrap 沙箱执行），但 PATH 上找不到；"
            "请安装 velites 或将 max_code_concurrency 设为 0 后重启"
        )
    if not reasons:
        return None
    return f"Agent Worker 启动预检失败：{'；'.join(reasons)}"
