from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from server.app.services.job_run_dir_lookup import (
    build_job_dir_index,
    derive_run_dir_from_index,
)
from server.app.services.run_dir_cleanup import cleanup_extra_runs_per_node, remove_path
from server.app.storage_paths import resolve_data_path

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


def cleanup_old_logs(
    conn: sqlite3.Connection,
    data_dir: Path,
    config: CleanupConfig,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Delete old node run log files and run directories past retention.

    When ``config.keep_only_latest_run_per_node`` is true (the default), any
    node with more than one run directory on disk will have all but the newest
    run directory removed. This prevents retried Pi nodes from accumulating
    unbounded ``events.jsonl`` files regardless of the retention window.
    """
    now = now or datetime.now(UTC)
    logs_removed = 0
    run_dirs_removed = 0

    if config.keep_only_latest_run_per_node:
        run_dirs_removed += cleanup_extra_runs_per_node(conn, data_dir)

    log_cutoff = now - timedelta(days=config.log_retention_days)
    run_dir_cutoff = now - timedelta(days=config.run_dir_retention_days)
    rows = conn.execute(
        """
        select id, job_id, node_key, log_path, run_dir, finished_at
        from node_runs
        where status in ('completed', 'failed') and finished_at != ''
        """
    ).fetchall()
    job_dir_index = build_job_dir_index(data_dir / "jobs", {row["job_id"] for row in rows})
    for row in rows:
        finished_at = row["finished_at"]
        try:
            finished = datetime.fromisoformat(finished_at)
            if finished.tzinfo is None:
                finished = finished.replace(tzinfo=UTC)
        except ValueError:
            continue
        log_path_str = row["log_path"]
        if log_path_str and finished <= log_cutoff:
            try:
                remove_path(resolve_data_path(log_path_str, data_dir, allow_missing=True))
                logs_removed += 1
            except Exception as exc:
                logger.warning("Failed to remove log %s: %s", log_path_str, exc)
        run_dir_str = row["run_dir"]
        if finished <= run_dir_cutoff:
            if not run_dir_str:
                run_dir = derive_run_dir_from_index(row["job_id"], row["node_key"], job_dir_index)
                if run_dir is not None:
                    run_dir_str = str(run_dir)
            if run_dir_str:
                try:
                    remove_path(resolve_data_path(run_dir_str, data_dir, allow_missing=True))
                    run_dirs_removed += 1
                except Exception as exc:
                    logger.warning("Failed to remove run_dir %s: %s", run_dir_str, exc)
    return logs_removed, run_dirs_removed
