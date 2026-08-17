"""Executor adapter contract + code-execution parameter carrier (P-0.5).

The single implicit code pool has exactly one adapter (``executors.code``);
the kind-registration machinery and the pi/openclaw adapters are retired
(schema v47). ``CodeCapabilityConfig`` carries the dispatch-resolved
schema/timeout/network into the Worker manifest — it is no longer a
persisted executor-definition shape.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from server.app.config_schema import validate_config_schema
from server.app.executors.models import ExecutionContext, ExecutionResult


class Executor(Protocol):
    id: str
    kind: str

    def supports(self, capability: str) -> bool:
        """Return whether this instance implements the capability."""

    def execute(self, context: ExecutionContext) -> ExecutionResult | None:
        """Execute one already-claimed Node and return a normalized result."""

    def cancel(self, execution_id: str) -> None:
        """Request cancellation for one execution when the adapter supports it."""


class LeaseRepository(Protocol):
    def heartbeat(self, lease_id: str, ttl_seconds: int) -> bool:
        """Renew a lease and return whether it is still active."""

    def finish(self, lease_id: str, result: ExecutionResult) -> bool:
        """Persist the final result for a lease and return success."""


class CodeCapabilityConfig(BaseModel):
    """Resolved code-execution parameters for one node dispatch."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    timeout_seconds: int = Field(default=600, ge=1)
    # Custom (DB-backed) code runs inside the velites OS sandbox
    # (EXEC-CODE-003), which denies network by default; nodes opt in via the
    # sandbox_network config key.
    sandbox_network: bool = False
    config_schema: dict[str, Any] = Field(default_factory=dict)

    @field_validator("config_schema", mode="after")
    @classmethod
    def _validate_config_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        validate_config_schema(value)
        return value
