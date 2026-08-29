"""Hot-reloaded claim controls for the Worker executor."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from worker.binary_resolution import resolve_binary

MAX_DYNAMIC_CONCURRENCY = 1024


def validate_claim_controls(capacity: Any, enabled: Any) -> None:
    if (
        isinstance(capacity, bool)
        or not isinstance(capacity, int)
        or not 1 <= capacity <= MAX_DYNAMIC_CONCURRENCY
    ):
        raise ValueError("最大并发数必须是 1 到 1024 的整数")
    if not isinstance(enabled, bool):
        raise ValueError("领取任务开关必须是布尔值")


def load_config(path: Path) -> dict[str, Any]:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("worker config must be a mapping")
    return config


def load_claim_controls(path: Path) -> tuple[int, bool]:
    config = load_config(path)
    capacity = config.get("max_concurrency")
    enabled = config.get("claim_enabled", False)
    validate_claim_controls(capacity, enabled)
    assert isinstance(capacity, int)
    assert isinstance(enabled, bool)
    return capacity, enabled


def load_code_concurrency(path: Path) -> int:
    """code 执行池容量（批次 2 协议 v2）；0 = 仅领取 agent 任务。

    与 Host 的 RegisterAgentWorkerRequest.max_code_concurrency 上限
    （agent_workers_contracts.py，le=1024）对齐。"""
    value = load_config(path).get("max_code_concurrency", 0)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= MAX_DYNAMIC_CONCURRENCY
    ):
        raise ValueError(f"code 并发数必须是 0 到 {MAX_DYNAMIC_CONCURRENCY} 的整数")
    return value


def hot_code_concurrency(current: int, loaded: int) -> tuple[int, bool]:
    """Hot-applied code pool capacity; returns (effective, rejected).

    Hot-opening code capacity (0 -> >0) requires a resolvable velites binary
    (EXEC-CODE-003 fail-closed), enforced at startup by preflight_error; a
    direct config-file edit must not bypass that guard. Resizing stays hot.
    """
    if loaded > 0 and current == 0 and resolve_binary("velites") is None:
        return current, True
    return loaded, False
