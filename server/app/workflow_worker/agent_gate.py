"""Per-pass Agent claim gates for the workflow worker's poll thread.

Three cheap in-memory gates keep the poll thread off the database when
thousands of agent candidates pile up behind saturated Workers:

- a batched active-request filter: one chunked query per pass (see
  ``server.app.agent_broker.batch``) replaces one ``has_active_request``
  round-trip per candidate;
- an enqueue-pool-full flag: once the bounded enqueue pool rejects a
  submission, the remaining agent candidates of this pass are skipped
  without further work (local executor candidates are unaffected);
- the stockpile limit (``server.app.workflow_worker.agent_stock``): pairs already stocked
  to their target are skipped, bounding bundle-build CPU/IO; enqueue
  submissions within the refresh window count toward the target.

All gates are advisory: the broker's unique one-active-request index and
the enqueue re-check on the pool thread stay the authoritative dedup.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from server.app.agent_broker import batch
from server.app.workflow_worker.agent_stock import StockSnapshot, load_stock_snapshot

if TYPE_CHECKING:
    from collections import deque

    from server.app.workflow_worker.ready import ReadyCandidate
    from server.app.workflow_worker.thread import WorkflowWorkerThread


@dataclass
class AgentPassState:
    """Agent claim-gate state owned by the worker thread.

    ``active_nodes`` / ``pool_full`` / ``stock_gated`` are per-pass (reset
    at the top of every poll); the stock snapshot persists across passes
    and refreshes on ``AgentStockConfig.refresh_seconds``.
    ``stock_enqueued`` counts enqueue-pool submissions per
    (workspace_id, agent_id) since the snapshot was loaded, so a frozen
    snapshot cannot over-release; it resets when the snapshot reloads.
    """

    active_nodes: set[tuple[str, str]] = field(default_factory=set)
    pool_full: bool = False
    stock_gated: int = 0
    stock_snapshot: StockSnapshot | None = None
    stock_loaded_at: float = 0.0
    stock_enqueued: dict[tuple[str, str], int] = field(default_factory=dict)

    def reset_pass(self) -> None:
        self.active_nodes = set()
        self.pool_full = False
        self.stock_gated = 0

    def force_refresh(self) -> None:
        """Expire the stock snapshot so the next pass reloads it (restock signal)."""
        self.stock_loaded_at = 0.0


def request_restock(worker: WorkflowWorkerThread) -> None:
    """Empty-claim signal: expire the stock snapshot and wake the poll loop."""
    worker._agent_pass.force_refresh()
    worker._wake_event.set()


def prepare_agent_pass(
    worker: WorkflowWorkerThread, queues: dict[str, deque[ReadyCandidate]]
) -> None:
    """Load the per-pass gate inputs in bulk, once per poll pass."""
    dispatch = worker.agent_dispatch
    if dispatch is None or not worker.settings.agent_definitions:
        return
    job_ids = _candidate_job_ids(worker, queues)
    if not job_ids:
        return
    state = worker._agent_pass
    state.active_nodes = batch.active_request_keys(dispatch.broker.database_dsn, sorted(job_ids))
    config = worker.settings.executor_runtime.agent_stock
    if not config.enabled:
        state.stock_snapshot = None
        state.stock_enqueued.clear()
        return
    now = time.monotonic()
    if state.stock_snapshot is None or now - state.stock_loaded_at >= config.refresh_seconds:
        state.stock_snapshot = load_stock_snapshot(dispatch.broker.database_dsn, config)
        state.stock_loaded_at = now
        state.stock_enqueued.clear()


def _candidate_job_ids(
    worker: WorkflowWorkerThread, queues: dict[str, deque[ReadyCandidate]]
) -> set[str]:
    """Job ids of candidates that may route to an Agent.

    Rough zero-DB filter on the route cache: candidates with a cached
    non-agent route are excluded; uncached candidates are kept, so the
    result is a superset (extra ids only cost extra rows in one query).
    """
    job_ids: set[str] = set()
    for workspace_id, queue in queues.items():
        for candidate in queue:
            cached = worker._route_cache.get(
                (workspace_id, candidate.definition.key, candidate.node.key)
            )
            if cached is not None and cached[1].kind != "agent":
                continue
            job_ids.add(str(candidate.job["id"]))
    return job_ids


def agent_claim_allowed(
    worker: WorkflowWorkerThread,
    workspace_id: str,
    job_id: str,
    node_key: str,
    agent_id: str,
) -> bool:
    """In-memory per-pass gates before config resolution; zero DB here."""
    state = worker._agent_pass
    if (job_id, node_key) in state.active_nodes:
        return False
    snapshot = state.stock_snapshot
    if snapshot is not None and not snapshot.allows(
        workspace_id, agent_id, extra=state.stock_enqueued.get((workspace_id, agent_id), 0)
    ):
        state.stock_gated += 1
        return False
    return True
