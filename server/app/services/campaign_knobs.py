"""Campaign knob resolution: watermark/batch_size defaults + ceilings.

创建与 preview 共用（PR #541 二轮 P2）：dry-run 接受的上限若比创建
宽松，就会确认一个随后无法创建的 campaign。batch_size 护栏口径——
rerun/upgrade ≤ campaigns.rerun_max_batch_size（每个切片都重进调度器的
ready set）；submit ≤ workflows.max_items_per_run（feeder 每批走
create_run 的 #358 上限）。watermark 是补货触发阈值而非容量承诺，仅做
>= 1 的下界检查。
"""

from __future__ import annotations

from typing import Any

from server.app.services.job_errors import InvalidOperationError


def resolve_watermark(campaigns_config: Any, watermark: int | None) -> int:
    """Watermark with the default applied and the >= 1 guard."""
    effective = campaigns_config.default_watermark if watermark is None else watermark
    if effective < 1:
        raise InvalidOperationError(
            f"watermark must be >= 1 (a replenishment trigger level), got {effective}"
        )
    return effective


def resolve_batch_size(
    campaigns_config: Any, workflows_config: Any, mode: str, batch_size: int | None
) -> int:
    """batch_size 的默认值与上限护栏（mode 感知，见模块 docstring）。"""
    effective = campaigns_config.default_batch_size if batch_size is None else batch_size
    if effective < 1:
        raise InvalidOperationError(f"batch_size must be >= 1, got {effective}")
    if mode in ("rerun", "upgrade") and effective > campaigns_config.rerun_max_batch_size:
        raise InvalidOperationError(
            f"{mode} campaign batch_size {effective} exceeds the rerun_max_batch_size"
            f" ceiling {campaigns_config.rerun_max_batch_size}"
            " (each slice re-enters the scheduler's ready set)"
        )
    max_items_per_run = workflows_config.max_items_per_run
    if mode == "submit" and max_items_per_run and effective > max_items_per_run:
        raise InvalidOperationError(
            f"submit campaign batch_size {effective} exceeds"
            f" workflows.max_items_per_run={max_items_per_run} (#358 guard)"
        )
    return effective
