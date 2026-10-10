"""Settle barrier for psycopg_pool maintenance workers (#1045, #1077).

With a ``reset`` callback (pools.py, #438) the pool hands every returned
connection to a maintenance worker (``ReturnConnection``), which rolls back
a dirty (INTRANS) return asynchronously. ``ConnectionPool.close()`` marks
the pool closed first, and a task that runs after that is silently
discarded: the returned connection is neither rolled back nor closed, so
its server-side session keeps the open transaction (snapshot and locks)
until Python's cyclic GC finalizes it. Whether the worker wins the race
against a close depends on CPU/GIL scheduling.

``settle_pool_returns`` is the one barrier every pool close goes through
(``pools.close_database_pools`` — production lifespan shutdown and test
boundaries alike) and that the per-test isolation reuses without closing
(``pools.settle_database_pools``). Lives apart from pools.py to keep the
pool registry module inside its size budget.
"""

from __future__ import annotations

import contextlib
import threading
from typing import Any

from psycopg_pool import ConnectionPool

# Upper bound for one pool's barrier. A wedged worker (e.g. a connect stuck
# against an unreachable server) breaks the barrier instead of stalling the
# caller; the close then proceeds exactly as it did before the barrier.
SETTLE_TIMEOUT_SECONDS = 5.0


class _WorkerCheckpoint:
    """Pool maintenance task that parks one pool worker on a shared barrier.

    psycopg_pool workers only ever call ``task.run()`` on dequeued tasks, so
    a duck-typed object is enough. A broken barrier (caller-side timeout)
    must not escape: the worker loop only swallows psycopg client errors and
    any other exception would kill the worker thread.
    """

    def __init__(self, barrier: threading.Barrier) -> None:
        self._barrier = barrier

    def run(self) -> None:
        with contextlib.suppress(threading.BrokenBarrierError):
            self._barrier.wait(SETTLE_TIMEOUT_SECONDS)


def settle_pool_returns(pool: ConnectionPool[Any]) -> None:
    """Block until every maintenance task queued before this call has run.

    One checkpoint per worker on a ``num_workers + 1`` barrier: the queue is
    FIFO and a parked worker cannot take a second checkpoint, so the barrier
    trips only after all workers finished everything queued before it. On
    timeout (a wedged worker) the barrier breaks and the caller proceeds.
    Tasks queued *after* this call are not covered — callers settle once
    nothing else returns connections any more (lifespan shutdown runs after
    every DB-using background thread stopped; test boundaries run between
    tests).
    """
    if pool.closed:
        return
    barrier = threading.Barrier(pool.num_workers + 1)
    for _ in range(pool.num_workers):
        pool.run_task(_WorkerCheckpoint(barrier))  # type: ignore[arg-type]
    with contextlib.suppress(threading.BrokenBarrierError):
        barrier.wait(SETTLE_TIMEOUT_SECONDS)
