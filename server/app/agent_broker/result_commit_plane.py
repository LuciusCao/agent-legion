"""Agent-plane construction and late binding for result group commit."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from server.app.agent_broker.mark_done_batch import (
    mark_done_many_with_retry as _mark_done_many_with_retry,
)
from server.app.agent_broker.result_commit_batcher import ResultCommitBatcher
from server.app.executors._lease_finish_batch import (
    finish_many_with_retry as _finish_many_with_retry,
)

if TYPE_CHECKING:
    from server.app.agent_broker import AgentExecutionBroker
    from server.app.executors.leases import ExecutorLeaseRepository
    from server.app.settings import Settings


def build_result_commit_batcher(
    settings: Settings, *, result_batching: bool
) -> ResultCommitBatcher | None:
    """Build the shared writer only when both lifecycle gates enable it."""
    # #591 C1: gate on the flag that starts the writer — a start_worker=False
    # plane must carry no batcher (its submit would park on an undrained future).
    if result_batching and settings.executor_runtime.agent_workers.result_commit_batching:
        return ResultCommitBatcher(None, None)
    return None


def bind_result_commit_arms(
    batcher: ResultCommitBatcher | None,
    *,
    executor_leases: ExecutorLeaseRepository,
    broker: AgentExecutionBroker,
) -> None:
    """Late-bind both retry-wrapped arms after their owners exist."""
    if batcher is None:
        return
    batcher.finish_many = partial(_finish_many_with_retry, executor_leases)
    # #609 P2-A: BOTH arms retry-wrapped (symmetry) — cross-replica
    # deployments (the single-replica probe is warning-only) can 40P01
    # either shared transaction; the isolation fallback's single-item
    # replays ride the same bound arm, so they retry too.
    batcher.mark_done_many = partial(_mark_done_many_with_retry, broker)
