from __future__ import annotations

import logging
import threading

from server.app.executors.models import ClaimedExecution, ExecutionContext, ExecutionResult
from server.app.executors.protocol import Executor, ExecutorResolver, LeaseRepository

logger = logging.getLogger(__name__)


class ExecutionRuntime:
    """Coordinates heartbeat lease renewal with executor adapter execution."""

    def __init__(
        self,
        leases: LeaseRepository,
        registry: ExecutorResolver,
        heartbeat_interval_seconds: float = 10,
        lease_ttl_seconds: int = 30,
    ) -> None:
        """Store lease, registry, heartbeat interval, and TTL dependencies."""
        self.leases = leases
        self.registry = registry
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.lease_ttl_seconds = lease_ttl_seconds

    def run(self, claim: ClaimedExecution, context: ExecutionContext) -> ExecutionResult:
        """Heartbeat and execute one claimed Node, then persist its final result."""
        executor = self.registry.require(claim.executor_id, claim.capability)
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
            result = ExecutionResult(
                status="failed",
                exit_code=1,
                error_message=str(exc),
                log_path=str(context.log_path),
            )
        finally:
            done_event.set()
            heartbeat_thread.join(timeout=self.heartbeat_interval_seconds + 5)

        if lease_lost_event.is_set():
            result = ExecutionResult(
                status="failed",
                exit_code=1,
                error_message="lease was lost during execution",
                log_path=str(context.log_path),
            )

        self.leases.finish(claim.lease_id, result)
        return result

    def _heartbeat_loop(
        self,
        claim: ClaimedExecution,
        executor: Executor,
        done_event: threading.Event,
        lease_lost_event: threading.Event,
    ) -> None:
        """Renew the lease periodically until execution finishes or the lease is lost."""
        while not done_event.is_set():
            if done_event.wait(self.heartbeat_interval_seconds):
                break
            try:
                active = self.leases.heartbeat(claim.lease_id, self.lease_ttl_seconds)
            except Exception:
                logger.exception(
                    "Heartbeat failed for lease %s execution %s",
                    claim.lease_id,
                    claim.execution_id,
                )
                active = False

            if done_event.is_set():
                break

            if not active:
                logger.warning(
                    "Lease %s no longer active for execution %s; cancelling",
                    claim.lease_id,
                    claim.execution_id,
                )
                try:
                    executor.cancel(claim.execution_id)
                except Exception:
                    logger.exception("Cancel request failed for execution %s", claim.execution_id)
                lease_lost_event.set()
                break
