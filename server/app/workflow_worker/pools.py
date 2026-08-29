"""Single implicit code pool for the workflow worker poll loop (P-0.5).

Every executor-routed node runs on one ThreadPoolExecutor keyed by
CODE_EXECUTOR_ID, sized from the instance settings ``code_capacity``. There
is no hot reload: a capacity change takes effect on restart, so the pool is
created once and only rebuilt if the configured size ever drifts.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from server.app.executors.models import CODE_EXECUTOR_ID

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread


def ensure_pools(worker: WorkflowWorkerThread) -> None:
    capacity = worker.settings.executor_runtime.code_capacity
    pool = worker.state.pools.get(CODE_EXECUTOR_ID)
    # ThreadPoolExecutor exposes no public max_workers getter.
    if pool is None or pool._max_workers != capacity:
        if pool is not None:
            pool.shutdown(wait=False)
        worker.state.pools[CODE_EXECUTOR_ID] = ThreadPoolExecutor(max_workers=capacity)
