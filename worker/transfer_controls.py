"""Transfer shaping controls for the Worker (static per process start)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from worker.runtime_controls import load_config


@dataclass(frozen=True)
class TransferControls:
    upload_max_concurrency: int = 4
    download_max_concurrency: int = 8
    # None = derive per loop iteration as 2x the current max_concurrency.
    upload_backlog_limit: int | None = None
    transfer_timeout_seconds: float = 120.0


def _positive_int(config: dict[str, Any], key: str, default: int) -> int:
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{key} 必须是正整数")
    return value


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
        upload_max_concurrency=_positive_int(config, "upload_max_concurrency", 4),
        download_max_concurrency=_positive_int(config, "download_max_concurrency", 8),
        upload_backlog_limit=backlog,
        transfer_timeout_seconds=timeout,
    )
