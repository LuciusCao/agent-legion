"""Hot-reloaded claim controls for the Worker executor."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

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
