"""Transfer shaping controls for the Worker (hot-reloaded each executor loop)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.concurrency_limits import MAX_DYNAMIC_CONCURRENCY
from worker.runtime.controls import load_config


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
    hard = 2 * worker_capacity if backlog_limit is None else backlog_limit
    # min 保证 soft < hard：hard=1 时 soft=0，下方区间判断退化为旧硬门；
    # hard<=0 只是防御性输入（校验已拒绝），depth >= hard 先命中、恒返回 0。
    soft = min(hard // 2, hard - 1)
    if depth >= hard:
        return 0
    return base if depth <= soft else base * (hard - depth) // (hard - soft)


def load_transfer_controls(path: Path) -> TransferControls:
    config = load_config(path)
    backlog = config.get("upload_backlog_limit")
    # #662 review：上限 = 默认背压门的 2×池容量。registry 的插入序跨拍
    # 稳定，relay 分片准入（relay_thread_limiter）只覆盖默认面（执行双
    # 池 + 上传 2×池）——超上限的积压会让尾部分片每拍被同一前缀挤掉、
    # 持续丢拍到租约过期，而非注释曾称的「下一拍重试」。收紧可自由；
    # 抬高须连同 MAX_INFLIGHT_SHARDS 与其契约测试同改。
    backlog_cap = 2 * MAX_DYNAMIC_CONCURRENCY
    if backlog is not None and (
        isinstance(backlog, bool) or not isinstance(backlog, int) or not 1 <= backlog <= backlog_cap
    ):
        raise ValueError(
            f"upload_backlog_limit 必须是 1 到 {backlog_cap} 的整数"
            "（relay 分片准入覆盖面：2×并发上限；抬高须同步扩 relay_thread_limiter）"
        )
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
