from __future__ import annotations

from server.app.executors.protocol import ExecutorResolver, LeaseRepository
from server.app.executors.runtime import ExecutionRuntime
from server.app.executors.runtime_config import ExecutorRuntimeConfig


def build_execution_runtime(
    leases: LeaseRepository,
    registry: ExecutorResolver,
    config: ExecutorRuntimeConfig,
) -> ExecutionRuntime:
    return ExecutionRuntime(
        leases,
        registry,
        heartbeat_interval_seconds=config.heartbeat_interval_seconds,
        lease_ttl_seconds=config.lease_ttl_seconds,
        heartbeat_failure_threshold=config.heartbeat_failure_threshold,
        cancellation_grace_seconds=config.cancellation_grace_seconds,
    )
