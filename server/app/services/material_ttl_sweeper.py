"""Slow-cadence driver thread for the materials TTL sweep.

Split from :mod:`material_ttl` (budget): the sweep functions are the
unit-testable core; this thread mirrors JobArtifactMaintenanceThread's loop
discipline around them.
"""

from __future__ import annotations

import logging
import threading

from server.app.db.dialect import ConnectSource
from server.app.services.material_ttl import (
    DEFAULT_SWEEP_INTERVAL_SECONDS,
    collect_expired_materials,
    expire_due_materials,
)
from server.app.storage import ObjectStorage

logger = logging.getLogger(__name__)


class MaterialTtlSweeperThread:
    """Slow-cadence driver; mirrors JobArtifactMaintenanceThread's discipline.

    The first run happens after one full interval: the sweep is low-urgency
    and a boot-time scan only competes with startup work.
    """

    def __init__(
        self,
        database_dsn: ConnectSource,
        storage: ObjectStorage | None,
        *,
        interval_seconds: float = DEFAULT_SWEEP_INTERVAL_SECONDS,
    ) -> None:
        # database_dsn: JobQueries facade or bare DSN (BOUNDARY-DATA-001, #187).
        self._dsn = database_dsn
        self._storage = storage
        self._interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="material-ttl-sweeper", daemon=True)
        self._thread.start()

    def run_once(self) -> None:
        # No object storage means no materials feature: rows can only reach
        # ready/expired through the storage-backed upload flow.
        if self._storage is None:
            return
        expired = expire_due_materials(self._dsn)
        if expired:
            logger.info("materials TTL sweep expired %d material(s)", expired)
        deleted = collect_expired_materials(self._dsn, self._storage)
        if deleted:
            logger.info("materials TTL sweep collected %d material(s)", deleted)

    def _loop(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            try:
                self.run_once()
            except Exception:
                logger.exception("materials TTL sweep failed")

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            self._thread = None
