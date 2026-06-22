from __future__ import annotations

import logging
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from server.app.storage_paths import derive_run_dir_from_log_path, resolve_data_path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CleanupConfig:
    log_retention_days: int = 30
    run_dir_retention_days: int = 30

    @classmethod
    def from_settings(cls, settings_config: dict) -> CleanupConfig:
        cfg = settings_config.get("cleanup", {})
        return cls(
            log_retention_days=int(cfg.get("log_retention_days", 30)),
            run_dir_retention_days=int(cfg.get("run_dir_retention_days", 30)),
        )


def _remove_path(path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.is_file():
            path.unlink()
    except OSError as exc:
        logger.warning("Failed to remove %s: %s", path, exc)


def cleanup_old_logs(
    conn: sqlite3.Connection,
    data_dir: Path,
    config: CleanupConfig,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Delete old node run log files and run directories past retention."""
    now = now or datetime.now(UTC)
    logs_removed = 0
    run_dirs_removed = 0

    log_cutoff = now - timedelta(days=config.log_retention_days)
    run_dir_cutoff = now - timedelta(days=config.run_dir_retention_days)

    rows = conn.execute(
        """
        select id, job_id, node_key, log_path, run_dir, finished_at
        from node_runs
        where status in ('completed', 'failed') and finished_at != ''
        """
    ).fetchall()

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
                log_path = resolve_data_path(log_path_str, data_dir, allow_missing=True)
                _remove_path(log_path)
                logs_removed += 1
            except Exception as exc:
                logger.warning("Failed to remove log %s: %s", log_path_str, exc)

        run_dir_str = row["run_dir"]
        if not run_dir_str and finished <= run_dir_cutoff:
            run_dir = derive_run_dir_from_log_path(
                row["log_path"], row["node_key"], row["job_id"], data_dir / "jobs"
            )
            if run_dir is not None:
                run_dir_str = str(run_dir)

        if run_dir_str and finished <= run_dir_cutoff:
            try:
                run_dir_path = resolve_data_path(run_dir_str, data_dir, allow_missing=True)
                _remove_path(run_dir_path)
                run_dirs_removed += 1
            except Exception as exc:
                logger.warning("Failed to remove run_dir %s: %s", run_dir_str, exc)

    return logs_removed, run_dirs_removed
