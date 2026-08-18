from __future__ import annotations

import logging
import threading
import time

from server.app.jobs import JobQueries
from server.app.services.log_cleanup import CleanupConfig, cleanup_old_logs
from server.app.settings import Settings

logger = logging.getLogger(__name__)


class WorkflowMaintenance:
    """Periodic maintenance for the workflow worker: old-log cleanup, token purge."""

    def __init__(self, job_db: JobQueries, settings: Settings) -> None:
        self.job_db = job_db
        self.settings = settings
        self._cleanup_config = CleanupConfig.from_settings(settings.config)
        self._cleanup_interval_seconds = float(
            settings.config.get("cleanup", {}).get("interval_seconds", 3600)
        )
        self._last_cleanup_at = 0.0
        self._cleanup_running = False

    def maybe_cleanup(self) -> None:
        """Kick the hourly cleanup onto a daemon thread; never stalls the poll loop."""
        now = time.monotonic()
        if now - self._last_cleanup_at < self._cleanup_interval_seconds:
            return
        if self._cleanup_running:
            return
        self._last_cleanup_at = now
        self._cleanup_running = True
        threading.Thread(target=self._run_cleanup, name="workflow-cleanup", daemon=True).start()

    def _run_cleanup(self) -> None:
        try:
            logs, run_dirs = cleanup_old_logs(
                self.job_db, self.settings.data_dir, self._cleanup_config
            )
            if logs or run_dirs:
                logger.info("Cleaned up %s old logs and %s old run directories", logs, run_dirs)
            tokens = self.job_db.delete_expired_scoped_tokens()
            if tokens:
                logger.info("Purged %s expired scoped tokens", tokens)
        except Exception:
            logger.exception("Failed to clean up old logs")
        finally:
            self._cleanup_running = False
