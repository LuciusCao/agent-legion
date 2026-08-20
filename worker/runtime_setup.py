"""Worker runtime startup: binary preflight followed by model discovery."""

from __future__ import annotations

from typing import Any

from worker.runtime_models import discover_effective_models
from worker.runtime_preflight import preflight_error


def prepare_runtime_models(config: dict[str, Any], *, code_concurrency: int = 0) -> str | None:
    """Validate runtime binaries and inject the effective model declarations."""
    error = preflight_error(config.get("runtimes") or [], code_concurrency=code_concurrency)
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
