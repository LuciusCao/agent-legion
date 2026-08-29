"""Worker runtime startup: binary preflight followed by model discovery."""

from __future__ import annotations

from typing import Any

from worker.runtime.catalog import resolve_config_runtimes
from worker.runtime.models import discover_effective_models
from worker.runtime.preflight import preflight_error


def prepare_runtime_models(config: dict[str, Any], *, code_concurrency: int = 0) -> str | None:
    """Resolve runtime declarations, validate binaries, inject effective models.

    executor 子进程直接读状态文件（原始 yaml），生效 runtimes 在这里按
    catalog 语义现算（探测 − disabled_runtimes，含旧 opt-in 键迁移），与
    config_store 读取路径共用同一入口（issue #254）。"""
    try:
        disabled, runtimes = resolve_config_runtimes(config)
    except ValueError as exc:
        return f"Agent Worker 启动预检失败：{exc}"
    config["disabled_runtimes"] = disabled
    config["runtimes"] = runtimes
    error = preflight_error(code_concurrency=code_concurrency)
    if error is not None:
        return error
    effective, discovery_errors = discover_effective_models(config)
    for runtime, detail in discovery_errors.items():
        print(
            f"运行时 {runtime!r} 模型发现失败，该 runtime 不会领取 Agent 任务：{detail}",
            flush=True,
        )
    config["models"] = effective
    return None
