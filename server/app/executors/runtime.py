from __future__ import annotations

import logging
import threading
from dataclasses import replace

from server.app.executors.cancellation import CancellationToken
from server.app.executors.contracts import Executor, LeaseRepository
from server.app.executors.models import (
    ClaimedExecution,
    ExecutionContext,
    ExecutionResult,
)

logger = logging.getLogger(__name__)


def _failed_result(context: ExecutionContext, message: str) -> ExecutionResult:
    return ExecutionResult(
        status="failed",
        exit_code=1,
        error_message=message,
        log_path=str(context.log_path),
    )


class ExecutionRuntime:
    def __init__(
        self,
        leases: LeaseRepository,
        executor: Executor,
        heartbeat_interval_seconds: float = 10,
        lease_ttl_seconds: int = 90,
        heartbeat_failure_threshold: int = 3,
        cancellation_grace_seconds: float = 5,
    ) -> None:
        self.leases = leases
        self.executor = executor
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.lease_ttl_seconds = lease_ttl_seconds
        self.heartbeat_failure_threshold = heartbeat_failure_threshold
        self.cancellation_grace_seconds = cancellation_grace_seconds
        self._active: dict[str, CancellationToken] = {}
        self._lock = threading.Lock()

    def run(self, claim: ClaimedExecution, context: ExecutionContext) -> ExecutionResult | None:
        # Single implicit code pool (P-0.5): one executor runs everything.
        executor = self.executor
        token = CancellationToken()
        with self._lock:
            self._active[claim.execution_id] = token

        context = replace(context, runtime={**context.runtime, "cancellation": token})
        done_event = threading.Event()
        lease_lost_event = threading.Event()

        heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(claim, executor, done_event, lease_lost_event),
            daemon=True,
        )
        heartbeat_thread.start()

        try:
            result = executor.execute(context)
        except Exception as exc:
            # #204 broad-except audit: this is the deliberate boundary between
            # the executor adapter layer and the lease finish path. Adapters
            # (subprocess harnesses, sandbox runners) are third-party surface:
            # their exception space is unbounded and NOT a business-exception
            # family we could enumerate. A stray exception must never skip
            # leases.finish below — the lease would dangle until the sweeper
            # expires it — so it is normalized into a failed result (with the
            # full traceback logged) instead of propagating.
            logger.exception(
                "Executor adapter %s failed for execution %s",
                claim.executor_id,
                claim.execution_id,
            )
            result = _failed_result(context, str(exc))
        finally:
            done_event.set()
            heartbeat_thread.join(timeout=self.heartbeat_interval_seconds + 5)
            with self._lock:
                self._active.pop(claim.execution_id, None)

        if result is None:
            # Blocking adapters must always return a result; treat a None here
            # as a contract violation instead of crashing the finish path.
            result = _failed_result(context, "executor returned no result")
        if lease_lost_event.is_set():
            result = _failed_result(context, "lease was lost during execution")

        self.leases.finish(claim.lease_id, result)
        return result

    def cancel(self, execution_id: str) -> None:
        with self._lock:
            token = self._active.get(execution_id)
        if token is not None:
            token.cancel()

    def _heartbeat_loop(
        self,
        claim: ClaimedExecution,
        executor: Executor,
        done_event: threading.Event,
        lease_lost_event: threading.Event,
    ) -> None:
        missed_heartbeats = 0
        while not done_event.is_set():
            if done_event.wait(self.heartbeat_interval_seconds):
                break
            try:
                active = self.leases.heartbeat(claim.lease_id, self.lease_ttl_seconds)
            except Exception:
                # #204 broad-except audit: the heartbeat loop is a daemon
                # thread whose death would silently stop lease renewal — the
                # execution would then be re-claimed elsewhere while still
                # running. Whatever the store raises (DB restart, driver
                # quirk) is treated as one missed heartbeat and counted by
                # the existing miss/threshold logic below; the full traceback
                # is logged so the root cause stays diagnosable.
                logger.exception("Heartbeat failed for lease %s", claim.lease_id)
                active = False

            if done_event.is_set():
                break

            if active:
                missed_heartbeats = 0
                continue

            missed_heartbeats += 1
            if missed_heartbeats < self.heartbeat_failure_threshold:
                logger.warning(
                    "Heartbeat miss %s/%s for lease %s",
                    missed_heartbeats,
                    self.heartbeat_failure_threshold,
                    claim.lease_id,
                )
                continue

            logger.warning("Lease %s no longer active; cancelling", claim.lease_id)
            try:
                self.cancel(claim.execution_id)
                executor.cancel(claim.execution_id)
            except Exception:
                # #204 broad-except audit: best-effort escalation of a lost
                # lease. Failing to cancel must not mask the lease-lost signal
                # — lease_lost_event.set() below is what terminates the
                # execution, and this thread must survive to set it. The
                # sweeper's lease expiry is the backstop if both cancels miss.
                logger.exception("Cancel request failed for execution %s", claim.execution_id)
            lease_lost_event.set()
            break
