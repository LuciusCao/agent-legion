from __future__ import annotations

import logging
import time

from server.app.jobs import JobQueries
from server.app.services.job_run_dir_backfill import backfill_node_run_dirs
from server.app.services.job_skill_version_backfill import backfill_node_run_skill_versions
from server.app.services.log_cleanup import CleanupConfig, cleanup_old_logs
from server.app.services.token_usage import backfill_missing_token_usage
from server.app.settings import Settings

logger = logging.getLogger(__name__)


class WorkflowMaintenance:
    """Periodic maintenance for the workflow worker: backfill missing run
    directories and clean up old log/run artifacts.
    """

    def __init__(self, job_db: JobQueries, settings: Settings) -> None:
        self.job_db = job_db
        self.settings = settings
        self._cleanup_config = CleanupConfig.from_settings(settings.config)
        self._cleanup_interval_seconds = float(
            settings.config.get("cleanup", {}).get("interval_seconds", 3600)
        )
        self._last_cleanup_at = 0.0

    def run_backfill(self) -> None:
        try:
            with self.job_db.connect() as conn:
                updated = backfill_node_run_dirs(conn, self.settings.data_dir)
            if updated:
                logger.info("Backfilled %s missing node run directories", updated)
        except Exception:
            logger.exception("Failed to backfill node run directories")

        try:
            with self.job_db.connect() as conn:
                result = backfill_node_run_skill_versions(conn, self.settings.data_dir)
            if result.node_runs_updated or result.manifests_updated:
                logger.info(
                    "Backfilled %s node run skill versions and refreshed %s manifests",
                    result.node_runs_updated,
                    result.manifests_updated,
                )
        except Exception:
            logger.exception("Failed to backfill node run skill versions")

        try:
            with self.job_db.connect() as conn:
                persisted = backfill_missing_token_usage(conn, self.settings.data_dir)
            if persisted:
                logger.info("Backfilled %s missing token usage rows", persisted)
        except Exception:
            logger.exception("Failed to backfill token usage")

    def maybe_cleanup(self) -> None:
        now = time.monotonic()
        if now - self._last_cleanup_at < self._cleanup_interval_seconds:
            return
        self._last_cleanup_at = now
        try:
            with self.job_db.connect() as conn:
                logs, run_dirs = cleanup_old_logs(
                    conn, self.settings.data_dir, self._cleanup_config
                )
            if logs or run_dirs:
                logger.info("Cleaned up %s old logs and %s old run directories", logs, run_dirs)
        except Exception:
            logger.exception("Failed to clean up old logs")
