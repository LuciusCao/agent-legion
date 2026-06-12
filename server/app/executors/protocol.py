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
