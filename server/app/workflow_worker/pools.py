"""Executor pool reconciliation for the workflow worker poll loop.

Extracted from the worker thread to keep it within its size budget. Pools
follow the live registry (hot-reloaded on executor publish/rollback/archive):
dropped executors get their pool shut down, and leases claimed but never
started are finished explicitly instead of reading as running until the
lease TTL sweeps them.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from server.app.executors.models import ExecutionResult

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread

logger = logging.getLogger(__name__)


def executor_capacities(worker: WorkflowWorkerThread) -> dict[str, int]:
    return {eid: worker.registry.global_capacity(eid) or 0 for eid in worker.registry.definitions()}


def ensure_pools(worker: WorkflowWorkerThread) -> None:
    # Reconcile with the live registry (hot-reloaded on executor
    # publish/rollback/archive): drop removed executors, add new ones,
    # resize on capacity change. The lease claim transaction stays the
    # authoritative capacity enforcement, so a mid-swap pool never
    # over-admits work.
    capacities = executor_capacities(worker)
    for executor_id in list(worker._pools):
        if executor_id not in capacities:
            worker._pools.pop(executor_id).shutdown(wait=False, cancel_futures=True)
            finish_unstarted_claims(worker, executor_id)
    for executor_id, capacity in capacities.items():
        pool = worker._pools.get(executor_id)
        # ThreadPoolExecutor exposes no public max_workers getter.
        if pool is None or pool._max_workers != capacity:
            if pool is not None:
                pool.shutdown(wait=False)
            worker._pools[executor_id] = ThreadPoolExecutor(max_workers=capacity)


def finish_unstarted_claims(worker: WorkflowWorkerThread, executor_id: str) -> None:
    """Fail leases whose pool was dropped before their future started.

    ``shutdown(cancel_futures=True)`` cancels queued futures without running
    them, so ``run_claim`` never finishes those leases; left alone they would
    read as running until the lease TTL sweeps them.
    """
    for execution_id, (claimed_executor, lease_id) in list(worker._future_claims.items()):
        if claimed_executor != executor_id:
            continue
        future = worker._futures.get(execution_id)
        if future is None or not future.cancelled():
            continue
        result = ExecutionResult(
            status="failed",
            exit_code=1,
            error_message="executor archived before the claimed execution started",
        )
        try:
            worker.leases.finish(lease_id, result)
        except Exception:
            logger.exception("failed to finish lease %s of dropped executor", lease_id)
        worker._futures.pop(execution_id, None)
        worker._future_claims.pop(execution_id, None)
