"""Lease hygiene for Host-local handlers and Agent Worker claims."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime

from server.app.agent_broker import AgentExecutionBroker
from server.app.executors.leases import ExecutorLeaseRepository

logger = logging.getLogger(__name__)


class SweeperThread:
    """Expire stale local leases, recover jobs, and requeue lost Agent claims."""

    def __init__(
        self,
        leases: ExecutorLeaseRepository,
        broker: AgentExecutionBroker,
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
            logger.exception("Agent claim sweep failed")
        try:
            stale = self._broker.fail_stale_definition_requests()
            if stale:
                logger.warning(
                    "failed Agent requests pinned to stale definitions: %s", ", ".join(stale)
                )
        except Exception:
            logger.exception("Agent stale-definition sweep failed")
        try:
            reaped = self._broker.reap_terminal_bundles()
            if reaped:
                logger.info("reaped %d terminal Agent bundle/archive files", reaped)
        except Exception:
            logger.exception("Agent bundle reap failed")
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

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            self._thread = None
