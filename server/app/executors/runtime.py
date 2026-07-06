from __future__ import annotations

import logging
import threading
from dataclasses import replace

from server.app.executors.cancellation import CancellationToken
from server.app.executors.models import ClaimedExecution, ExecutionContext, ExecutionResult
from server.app.executors.protocol import Executor, ExecutorResolver, LeaseRepository

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
        registry: ExecutorResolver,
        heartbeat_interval_seconds: float = 10,
        lease_ttl_seconds: int = 90,
        heartbeat_failure_threshold: int = 3,
        cancellation_grace_seconds: float = 5,
    ) -> None:
        self.leases = leases
        self.registry = registry
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.lease_ttl_seconds = lease_ttl_seconds
        self.heartbeat_failure_threshold = heartbeat_failure_threshold
        self.cancellation_grace_seconds = cancellation_grace_seconds
        self._active: dict[str, CancellationToken] = {}
        self._lock = threading.Lock()

    def run(self, claim: ClaimedExecution, context: ExecutionContext) -> ExecutionResult:
        executor = self.registry.require(claim.executor_id, claim.capability)
        token = CancellationToken()
        with self._lock:
            self._active[claim.execution_id] = token

        context = replace(context, runtime={"cancellation": token})
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
                logger.exception("Cancel request failed for execution %s", claim.execution_id)
            lease_lost_event.set()
            break
