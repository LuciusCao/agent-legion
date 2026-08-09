from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from server.app.services.cleanup_sweep import (
    cleanup_extra_runs_per_node,
    sweep_expired_node_runs,
)
from server.app.services.job_dir_index import build_job_dir_index

if TYPE_CHECKING:
    from server.app.jobs import JobQueries

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CleanupConfig:
    log_retention_days: int = 7
    run_dir_retention_days: int = 3
    keep_only_latest_run_per_node: bool = True

    @classmethod
    def from_settings(cls, settings_config: dict) -> CleanupConfig:
        cfg = settings_config.get("cleanup", {})
        return cls(
            log_retention_days=int(cfg.get("log_retention_days", 7)),
            run_dir_retention_days=int(cfg.get("run_dir_retention_days", 3)),
            keep_only_latest_run_per_node=bool(cfg.get("keep_only_latest_run_per_node", True)),
        )


def _expired_job_ids(db: JobQueries, cutoff: datetime) -> set[str]:
    """Return job ids with terminally finished node runs older than ``cutoff``."""
    with db._connect_read() as conn:
        rows = conn.execute(
            """
            select distinct job_id from node_runs
            where status in ('completed', 'failed') and finished_at is not null
              and finished_at < %s
            """,
            (cutoff,),
        )
        return {row["job_id"] for row in rows}


def cleanup_old_logs(
    db: JobQueries,
    data_dir: Path,
    config: CleanupConfig,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Delete old node run log files and run directories past retention.

    When ``config.keep_only_latest_run_per_node`` is true (the default), any
    node with more than one run directory on disk will have all but the newest
    run directory removed. This prevents retried Pi nodes from accumulating
    unbounded ``events.jsonl`` files regardless of the retention window.

    Filesystem deletions never happen inside a database transaction: expired
    rows are paged in bounded chunks over short-lived read connections and
    the accompanying ``node_runs`` updates are flushed in short write
    transactions (see ``cleanup_sweep``).
    """
    now = now or datetime.now(UTC)
    run_dirs_removed = 0

    if config.keep_only_latest_run_per_node:
        run_dirs_removed += cleanup_extra_runs_per_node(db, data_dir)

    log_cutoff = now - timedelta(days=config.log_retention_days)
    run_dir_cutoff = now - timedelta(days=config.run_dir_retention_days)
    cutoff = max(log_cutoff, run_dir_cutoff)
    job_dir_index = build_job_dir_index(db, data_dir, _expired_job_ids(db, cutoff))
    logs_removed, swept_run_dirs = sweep_expired_node_runs(
        db, data_dir, log_cutoff, run_dir_cutoff, job_dir_index
    )
    return logs_removed, run_dirs_removed + swept_run_dirs
