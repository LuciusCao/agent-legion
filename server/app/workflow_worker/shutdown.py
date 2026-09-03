"""Graceful shutdown for the workflow worker thread (#389 split from thread.py).

The stop sequence (cancel in-flight executions, drain futures, shut pools)
moved here when the pure-remote changes (#389: nullable runtime, empty
futures by construction) outgrew the parent's size budget.
"""

from __future__ import annotations

import logging
from concurrent.futures import wait
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread

logger = logging.getLogger(__name__)


def stop_worker(worker: WorkflowWorkerThread, timeout: float = 3) -> None:
    """Stop the poll loop, cancel in-flight executions, drain and shut pools.

    Pure-remote mode (#389) has no runtime and structurally no in-flight
    local futures, so the cancel loop below is naturally empty there.
    """
    worker.stop_event.set()
    worker.state.wake_event.set()
    if worker._thread is not None:
        worker._thread.join(timeout=timeout)
    grace = getattr(worker.runtime, "cancellation_grace_seconds", 5)
    for execution_id in list(worker.state.futures):
        try:
            if worker.runtime is not None:
                worker.runtime.cancel(execution_id)
        except Exception:
            # #204 broad-except audit: shutdown safety net — every remaining
            # execution gets its cancel attempted; one failing cancel must not
            # skip the others or break out of the shutdown sequence below
            # (pool shutdown, state cleanup).
            logger.exception("failed to cancel execution %s during shutdown", execution_id)
    done, pending = wait(list(worker.state.futures.values()), timeout=max(timeout, grace))
    for future in done:
        try:
            future.result()
        except Exception:
            # #204 broad-except audit: same as reap_futures — the failure was
            # already reported and compensated by run_claim; shutdown must
            # keep draining the remaining futures instead of aborting
            # mid-loop with pools still registered.
            logger.exception("workflow future failed during shutdown")
    if pending:
        logger.warning("%s workflow future(s) still active after shutdown timeout", len(pending))
    worker.state.futures.clear()
    worker.state.future_claims.clear()
    for pool in worker.state.pools.values():
        pool.shutdown(wait=False, cancel_futures=True)
    worker.state.pools.clear()
