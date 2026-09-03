"""Execution submission and future reaping for the workflow worker.

Extracted from the worker thread to keep it within its size budget. Claiming
lives in ``server.app.workflow_worker.schedule``; once a lease is claimed the
functions here submit the execution to the executor pool and reap finished
futures.

Pure-remote mode (#389): with ``code_capacity == 0`` the host assembles no
local executor stack at all, so ``submit_claim`` never runs — local claims
are structurally impossible (the capacity snapshot reports zero and the
lease claim transaction rejects ``global_capacity == 0``). The assertions
here are a tripwire against that contract drifting.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from server.app.executors.models import (
    ClaimedExecution,
    ExecutionContext,
    ExecutionResult,
)

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread

logger = logging.getLogger(__name__)


def submit_claim(
    worker: WorkflowWorkerThread,
    executor_id: str,
    claim: ClaimedExecution,
    context: ExecutionContext,
) -> None:
    """Submit a claimed execution to the executor pool and register it.

    The done callback wakes the poll loop so capacity freed by this
    execution is refilled on the next pass instead of after the idle
    backoff.
    """
    if worker.settings.executor_runtime.code_capacity <= 0:
        # Not reachable while the composition root honors the contract (the
        # local path is gated on the same config), but failing loudly here is
        # far better than queuing onto a pool that pure-remote mode never
        # built: the lease would expire into the sweeper's failure path.
        raise RuntimeError("submit_claim reached with code_capacity == 0 (pure-remote mode)")
    pool = worker.state.pools[executor_id]
    future = pool.submit(run_claim, worker, claim, context)
    future.add_done_callback(lambda _f: worker.state.wake_event.set())
    worker.state.futures[claim.execution_id] = future
    worker.state.future_claims[claim.execution_id] = (executor_id, claim.lease_id)


def run_claim(
    worker: WorkflowWorkerThread, claim: ClaimedExecution, context: ExecutionContext
) -> ExecutionResult | None:
    try:
        # Unreachable with runtime=None (pure-remote): submit_claim is the
        # only caller and raises first when code_capacity == 0.
        assert worker.runtime is not None
        return worker.runtime.run(claim, context)
    except Exception as exc:
        # #204 broad-except audit: pool-thread safety net. This function runs
        # on an executor pool thread whose unhandled exception would be
        # swallowed into the future by ThreadPoolExecutor — the lease would
        # never be finished here and the node would hang until the sweeper
        # expires it. ExecutionRuntime.run already normalizes executor
        # adapter errors, so reaching this catch means the runtime itself
        # failed (e.g. the lease finish inside it); the lease is finished as
        # failed with the full traceback logged, and a failed result is
        # returned so the worker's accounting stays consistent.
        logger.exception("workflow execution %s failed", claim.execution_id)
        result = ExecutionResult(
            status="failed",
            exit_code=1,
            error_message=str(exc),
            log_path=str(context.log_path),
        )
        worker.leases.finish(claim.lease_id, result)
        return result


def reap_futures(worker: WorkflowWorkerThread) -> None:
    for execution_id in list(worker.state.futures):
        future = worker.state.futures[execution_id]
        if future.done():
            try:
                future.result()
            except Exception:
                # #204 broad-except audit: reaping must continue — the worker
                # bookkeeping (futures/future_claims entries) is popped below
                # regardless, so one poisoned future cannot wedge the state
                # dict. run_claim already reported and compensated the
                # failure; this catch only guards against bookkeeping-time
                # surprises (e.g. a cancel racing the result). The traceback
                # is logged; nothing is masked because the result is
                # discarded by design.
                logger.exception("workflow future %s failed", execution_id)
            worker.state.futures.pop(execution_id, None)
            worker.state.future_claims.pop(execution_id, None)
