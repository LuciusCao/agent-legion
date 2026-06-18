from typing import Protocol

from server.app.executors.models import ExecutionContext, ExecutionResult


class Executor(Protocol):
    id: str
    kind: str

    def supports(self, capability: str) -> bool:
        """Return whether this instance implements the capability."""

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        """Execute one already-claimed Node and return a normalized result."""

    def cancel(self, execution_id: str) -> None:
        """Request cancellation for one execution when the adapter supports it."""


class LeaseRepository(Protocol):
    def heartbeat(self, lease_id: str, ttl_seconds: int) -> bool:
        """Renew a lease and return whether it is still active."""

    def finish(self, lease_id: str, result: ExecutionResult) -> bool:
        """Persist the final result for a lease and return success."""


class ExecutorResolver(Protocol):
    def require(self, executor_id: str, capability: str) -> Executor:
        """Return an executor that implements *capability* for the given ID."""
