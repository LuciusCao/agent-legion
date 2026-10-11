"""Hot-reloaded claim controls for the Worker executor."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from shared.code_sandbox import resolve_sandbox_binary
from shared.concurrency_limits import MAX_DYNAMIC_CONCURRENCY
from worker import worker_declarations

logger = logging.getLogger(__name__)
# 已移除的配置键（#452：`capabilities` 自 #284 起即 no-op）：存量 worker.yaml
# 残留时剥离并每进程告警一次，不让 Worker 因旧键启动失败。两条读取路径共用
# 这一处（#1023）：WorkerConfigStore 经 config_validation.validate_config，
# 直接 `executor.py --config` 经下方 load_config——放在这一层是因为
# config_validation 已依赖本模块（反向 import 成环）。
_REMOVED_KEYS = frozenset({"capabilities"})
_warned_removed: set[str] = set()


def strip_removed_keys(raw: dict[str, Any]) -> dict[str, Any]:
    """Drop retired keys (warning once per process per key); returns a copy."""
    for key in sorted((_REMOVED_KEYS & raw.keys()) - _warned_removed):
        _warned_removed.add(key)
        logger.warning("config key %r was removed (issue #452) and is ignored; delete it", key)
    return {key: value for key, value in raw.items() if key not in _REMOVED_KEYS}


def validate_claim_controls(capacity: Any, enabled: Any) -> None:
    if (
        isinstance(capacity, bool)
        or not isinstance(capacity, int)
        or not 1 <= capacity <= MAX_DYNAMIC_CONCURRENCY
    ):
        raise ValueError(f"最大并发数必须是 1 到 {MAX_DYNAMIC_CONCURRENCY} 的整数")
    if not isinstance(enabled, bool):
        raise ValueError("领取任务开关必须是布尔值")


def load_config(path: Path) -> dict[str, Any]:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("worker config must be a mapping")
    # #1023：executor 每个 pass 热读本函数，告警由 strip_removed_keys 的
    # 进程级去重保证只出一次。
    return strip_removed_keys(config)


def load_claim_controls(path: Path) -> tuple[int, bool, Any]:
    """Hot-read (max_concurrency, claim_enabled, raw ramp_up #471 原块透传)。"""
    config = load_config(path)
    capacity = config.get("max_concurrency")
    enabled = config.get("claim_enabled", False)
    validate_claim_controls(capacity, enabled)
    assert isinstance(capacity, int) and isinstance(enabled, bool)
    return capacity, enabled, config.get("ramp_up")


def load_code_concurrency(path: Path) -> int:
    """code 执行池容量（0 = 仅 agent）；上限与 Host 注册契约引用同一常量。"""
    value = load_config(path).get("max_code_concurrency", 0)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= MAX_DYNAMIC_CONCURRENCY
    ):
        raise ValueError(f"code 并发数必须是 0 到 {MAX_DYNAMIC_CONCURRENCY} 的整数")
    return value


def load_node_concurrency_limits(path: Path) -> dict[str, int]:
    """节点级并发上限（#1158）：{node_key: N}，缺省/空 = 不限制。

    与 load_claim_controls 同契约：非法值抛 ValueError（启动预检 fail-fast，
    热更保留旧值）。校验规则单一来源在 worker_declarations（config 入口与
    热读共用）。
    """
    return worker_declarations.normalize_node_concurrency_limits(
        load_config(path).get("node_concurrency_limits", {})
    )


def hot_code_concurrency(current: int, loaded: int) -> tuple[int, bool]:
    """Hot-applied code pool capacity; returns (effective, rejected).

    Hot-opening code capacity (0 -> >0) requires a resolvable sandbox wrapper
    (velites-sandbox or velites, EXEC-CODE-003 fail-closed), enforced at
    startup by preflight_error; a direct config-file edit must not bypass
    that guard. Resizing stays hot.
    """
    if loaded > 0 and current == 0 and resolve_sandbox_binary() is None:
        return current, True
    return loaded, False
