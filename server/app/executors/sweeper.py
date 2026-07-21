"""Standalone lease sweeper (phase 3, task 8).

The sweeper owns all periodic lease hygiene so the workflow worker thread only
claims and runs work (Decision 11). Each tick it sweeps expired remote claims,
expires stale leases, recovers orphaned running jobs, and renews the leases
backing live remote executions (Decision 6) — without that renewal, submit-only
remote leases would be expired while their executions are still in flight.

The sweeper is safe to run in multiple replicas: every step funnels through
PostgreSQL transactions with conditional writes, so expiry and claim requeues
stay single-winner across processes.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.remote_broker import RemoteExecutionBroker

logger = logging.getLogger(__name__)


class SweeperThread:
    """Periodically expire stale leases, recover orphaned jobs, sweep remote claims,
    and renew leases backing live remote executions (Decision 6/11)."""

    def __init__(
        self,
        leases: ExecutorLeaseRepository,
        broker: RemoteExecutionBroker,
        interval_seconds: float = 5.0,
        lease_ttl_seconds: int = 90,
    ) -> None:
        self._leases = leases
        self._broker = broker
        self._interval_seconds = interval_seconds
        self._lease_ttl_seconds = lease_ttl_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        # The startup sweep runs synchronously so stale state left by a previous
        # process is cleaned before the server starts accepting work.
        self._sweep_once()

        def _loop() -> None:
            while not self._stop_event.wait(self._interval_seconds):
                self._sweep_once()

        self._thread = threading.Thread(target=_loop, name="executor-lease-sweeper", daemon=True)
        self._thread.start()

    def _sweep_once(self) -> None:
        now = datetime.now(UTC)
        try:
            self._broker.sweep_expired_claims()
        except Exception:
            logger.exception("remote claim sweep failed")
        try:
            expired = self._leases.expire_stale(now)
            if expired:
                logger.warning("expired stale workflow executions: %s", ", ".join(expired))
        except Exception:
            logger.exception("lease expiry sweep failed")
        try:
            recovered = self._leases.recover_orphaned_running_jobs(now)
            if recovered:
                logger.warning("recovered orphaned running jobs: %s", ", ".join(recovered))
        except Exception:
            logger.exception("orphaned job recovery failed")
        try:
            active_lease_ids = self._broker.active_lease_ids()
        except Exception:
            logger.exception("active remote lease listing failed")
            return
        for lease_id in active_lease_ids:
            try:
                self._leases.heartbeat(lease_id, self._lease_ttl_seconds)
            except Exception:
                logger.exception("lease renewal failed for %s", lease_id)

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            self._thread = None
