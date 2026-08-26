"""Execution submission and future reaping for the workflow worker.

Extracted from the worker thread to keep it within its size budget. Claiming
lives in ``server.app.workflow_worker.schedule``; once a lease is claimed the
functions here submit the execution to the executor pool and reap finished
futures.
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
    pool = worker._pool_for(executor_id)
    future = pool.submit(run_claim, worker, claim, context)
    future.add_done_callback(lambda _f: worker.state.wake_event.set())
    worker.state.futures[claim.execution_id] = future
    worker.state.future_claims[claim.execution_id] = (executor_id, claim.lease_id)


def run_claim(
    worker: WorkflowWorkerThread, claim: ClaimedExecution, context: ExecutionContext
) -> ExecutionResult | None:
    try:
        return worker.runtime.run(claim, context)
    except Exception as exc:
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
                logger.exception("workflow future %s failed", execution_id)
            worker.state.futures.pop(execution_id, None)
            worker.state.future_claims.pop(execution_id, None)
