"""Worker 启动预检：声明的 runtime 所需二进制必须可解析。

声明了 runtime 但找不到对应二进制时，Worker 照常注册、claim 后 spawn
即失败，任务在 Host 侧空转重试——与"注册失败"相比更难发现。注册前 fail
loudly，把部署缺口变成明确的启动错误。

探测规则：runtime 钉死命令构建器与二进制（agent 配置治理 phase 3 起，
manifest 的 execution.binary 由 Agent 定义的 runtime 决定：pi → pi、
velites → velites），Worker 本地按同一映射要求对应二进制可解析；
openclaw 不探测（Host dispatch 对其本就 fail-fast，不存在 claim 后
spawn 的路径）。二进制解析（自带副本 data/bin 优先、PATH 兜底）统一走
``worker/binary_resolution.py::resolve_binary``。
"""

from __future__ import annotations

from collections.abc import Iterable

from worker import binary_resolution
from worker.binary_resolution import resolve_binary

# runtime -> 可接受的 argv[0] 候选（任一可解析即通过探测）。
RUNTIME_BINARY_CANDIDATES = {"pi": ("pi",), "velites": ("velites",)}


def missing_runtime_binaries(runtimes: Iterable[str]) -> dict[str, tuple[str, ...]]:
    """Return {runtime: candidates} for declared runtimes with no resolvable candidate."""
    missing: dict[str, tuple[str, ...]] = {}
    for runtime in sorted({str(value) for value in runtimes}):
        candidates = RUNTIME_BINARY_CANDIDATES.get(runtime)
        if candidates and not any(resolve_binary(binary) for binary in candidates):
            missing[runtime] = candidates
    return missing


def preflight_error(runtimes: Iterable[str], *, code_concurrency: int = 0) -> str | None:
    """Human-readable startup error when a declared runtime lacks its binary.

    ``code_concurrency`` > 0 additionally requires velites: every code
    execution runs through ``velites sandbox wrap`` (EXEC-CODE-003,
    fail-closed), even when the Worker declares no velites *agent* runtime."""
    bundled_dir = binary_resolution.BUNDLED_BINARY_DIR
    missing = missing_runtime_binaries(runtimes)
    reasons = [
        f"运行时 {runtime!r} 需要可执行文件 {' 或 '.join(repr(b) for b in candidates)}，"
        f"但 {bundled_dir} 与 PATH 上都找不到；请安装对应二进制"
        "（自带副本或 PATH 均可）并确认可执行，"
        "或从 runtimes 配置移除该运行时后重启"
        for runtime, candidates in missing.items()
    ]
    if code_concurrency > 0 and "velites" not in missing and resolve_binary("velites") is None:
        reasons.append(
            "声明了 code 执行容量（max_code_concurrency > 0）需要可执行文件 'velites'"
            "（code 任务统一经 velites sandbox wrap 沙箱执行），但 "
            f"{bundled_dir} 与 PATH 上都找不到；"
            "请安装 velites（仓库内部署可执行 scripts/ensure-velites.sh --dest data/bin"
            " 构建并安置自带副本）或将 max_code_concurrency 设为 0 后重启"
        )
    if not reasons:
        return None
    return f"Agent Worker 启动预检失败：{'；'.join(reasons)}"
