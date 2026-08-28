"""Lease hygiene for Host-local handlers and Agent Worker claims."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime

from server.app.agent_broker import AgentExecutionBroker
from server.app.agent_broker.unclaimable import fail_unclaimable_model_requests
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
        skill_sweeper: Callable[[], int] | None = None,
    ) -> None:
        self._leases = leases
        self._broker = broker
        self._interval_seconds = interval_seconds
        self._lease_ttl_seconds = lease_ttl_seconds
        # Leak GC for the skills runs dir (stale execution snapshots), e.g.
        # ``SkillManager.sweep_stale_executions``. Optional: tests and
        # replicas without a local skill manager pass None.
        self._skill_sweeper = skill_sweeper
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        # The startup sweep runs synchronously so stale state left by a previous
        # process is cleaned before the server starts accepting work. Terminal-
        # bundle reap is excluded (#139): it is idempotent GC, and its first
        # pass scans every terminal request in history — far too expensive for
        # the readiness-critical startup path. It runs in the loop below.
        self._sweep_once(reap_bundles=False)

        def _loop() -> None:
            while not self._stop_event.wait(self._interval_seconds):
                self._sweep_once()

        self._thread = threading.Thread(target=_loop, name="executor-lease-sweeper", daemon=True)
        self._thread.start()

    def _sweep_once(self, *, reap_bundles: bool = True) -> None:
        # #204 broad-except audit: every catch below is the deliberate safety
        # net of a periodic background sweep. The thread must survive ANY
        # failure of ANY sub-sweep — otherwise one flaky sub-sweep (e.g. a
        # transient DB error during claim expiry) would permanently stop lease
        # expiry, orphan recovery, and bundle GC for the whole process. Each
        # block logs the full traceback (logger.exception) so the failure is
        # diagnosable, and each is scoped to exactly one sub-sweep so an early
        # failure never skips the remaining ones. Converting these to narrow
        # business-exception catches would reintroduce the lost-thread
        # failure mode this loop exists to prevent.
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
            unclaimable = fail_unclaimable_model_requests(self._broker)
            if unclaimable:
                logger.warning("failed unclaimable Agent requests: %s", ", ".join(unclaimable))
        except Exception:
            logger.exception("Agent stale-request sweep failed")
        if reap_bundles:
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
        if self._skill_sweeper is not None:
            try:
                swept = self._skill_sweeper()
                if swept:
                    logger.info("swept %d stale skill execution dirs", swept)
            except Exception:
                logger.exception("skill execution-dir sweep failed")

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            self._thread = None
