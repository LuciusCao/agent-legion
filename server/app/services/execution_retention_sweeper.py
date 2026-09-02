"""Slow-cadence driver thread for the execution retention sweep.

Split from :mod:`execution_retention` (budget): the sweep functions are the
unit-testable core; this thread mirrors MaterialTtlSweeperThread's loop
discipline around them. Like the artifact GC / artifact maintenance /
materials TTL threads, it runs only on the single replica that owns the
sweeper role (``sweeper_enabled``).
"""

from __future__ import annotations

import logging
import threading

from server.app.db.dialect import ConnectSource
from server.app.services.execution_retention import (
    DEFAULT_SWEEP_INTERVAL_SECONDS,
    sweep_expired_executions,
)

logger = logging.getLogger(__name__)


class ExecutionRetentionThread:
    """Slow-cadence driver; mirrors MaterialTtlSweeperThread's discipline.

    The first run happens after one full interval: the sweep is low-urgency
    and a boot-time scan of a large expired tail only competes with startup
    work (the same reasoning as the material TTL thread).
    """

    def __init__(
        self,
        database_dsn: ConnectSource,
        *,
        interval_seconds: float = DEFAULT_SWEEP_INTERVAL_SECONDS,
    ) -> None:
        # database_dsn: JobQueries facade or bare DSN (BOUNDARY-DATA-001, #187).
        self._dsn = database_dsn
        self._interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="execution-retention-sweeper", daemon=True
        )
        self._thread.start()

    def run_once(self) -> None:
        sweep_expired_executions(self._dsn)

    def _loop(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            try:
                self.run_once()
            except Exception:
                # #204 broad-except audit: the sweeper thread's life support
                # (same discipline as MaterialTtlSweeperThread). run_once
                # pages three tables with fresh settings reads — a DB restart
                # or settings-read failure mid-pass must not kill the only
                # thread performing retention; the traceback is logged and
                # the next interval is the retry. Committed batches stay
                # deleted (per-batch transactions in _sweep_pages), so the
                # retry resumes from the persisted cursor, not from zero.
                logger.exception("execution retention sweep failed")

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            self._thread = None
