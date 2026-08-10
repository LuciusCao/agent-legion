"""Transfer shaping controls for the Worker (hot-reloaded each executor loop)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from worker.runtime_controls import MAX_DYNAMIC_CONCURRENCY, load_config


@dataclass(frozen=True)
class TransferControls:
    upload_max_concurrency: int = 4
    download_max_concurrency: int = 8
    # None = derive per loop iteration as 2x the current max_concurrency.
    upload_backlog_limit: int | None = None
    transfer_timeout_seconds: float = 120.0


def _bounded_int(config: dict[str, Any], key: str, default: int, max_value: int) -> int:
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= max_value:
        raise ValueError(f"{key} 必须是 1 到 {max_value} 的整数")
    return value


def claim_availability(
    base: int, depth: int, worker_capacity: int, backlog_limit: int | None
) -> int:
    """背压门渐变：soft=hard//2 以下全量、hard 归零、区间内线性衰减；hard<=1 退化为旧硬门。"""
    hard = backlog_limit or 2 * worker_capacity
    # min 只兜住 hard<=0 的防御性输入；hard>=1 时等价于 hard//2 且保证 soft < hard，
    # hard=1 时下方区间判断自然退化为旧硬门语义。
    soft = min(hard // 2, hard - 1)
    if depth >= hard:
        return 0
    return base if depth <= soft else base * (hard - depth) // (hard - soft)


def load_transfer_controls(path: Path) -> TransferControls:
    config = load_config(path)
    backlog = config.get("upload_backlog_limit")
    if backlog is not None and (
        isinstance(backlog, bool) or not isinstance(backlog, int) or backlog < 1
    ):
        raise ValueError("upload_backlog_limit 必须是正整数")
    timeout = float(config.get("transfer_timeout_seconds", 120))
    if timeout <= 0:
        raise ValueError("transfer_timeout_seconds 必须是正数")
    return TransferControls(
        upload_max_concurrency=_bounded_int(
            config, "upload_max_concurrency", 4, MAX_DYNAMIC_CONCURRENCY
        ),
        download_max_concurrency=_bounded_int(
            config, "download_max_concurrency", 8, MAX_DYNAMIC_CONCURRENCY
        ),
        upload_backlog_limit=backlog,
        transfer_timeout_seconds=timeout,
    )
