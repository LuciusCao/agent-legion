"""Per-pass Agent claim gates for the workflow worker's poll thread.

Four cheap in-memory gates keep the poll thread off the database when
thousands of agent candidates pile up behind saturated Workers:

- a batched active-request filter (``server.app.agent_broker.batch``):
  one chunked query per pass replaces per-candidate DB round-trips;
- an in-flight submission set: candidates already submitted to the
  enqueue pool are skipped until the pool closure removes them;
- an enqueue-pool-full flag: once the bounded enqueue pool rejects a
  submission, the remaining agent candidates of this pass are skipped;
- the stockpile limit (``agent_stock``): pairs stocked to target are
  skipped; submissions within the refresh window count toward target.

All gates are advisory: the broker's unique one-active-request index and
the enqueue re-check on the pool thread stay the authoritative dedup.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from server.app.agent_broker import batch
from server.app.services.agent_service import published_agent_definitions
from server.app.workflow_worker.agent_stock import StockSnapshot, load_stock_snapshot

if TYPE_CHECKING:
    from collections import deque

    from server.app.workflow_worker.ready import ReadyCandidate
    from server.app.workflow_worker.thread import WorkflowWorkerThread


@dataclass
class AgentPassState:
    """Agent claim-gate state owned by the worker thread.

    Per-pass (reset every poll): ``active_nodes`` / ``pool_full`` /
    ``stock_gated``. The stock snapshot persists across passes and
    refreshes on ``AgentStockConfig.refresh_seconds``; ``stock_enqueued``
    counts submissions per pair since the snapshot loaded, closing the
    frozen-window over-release hole, and resets with the snapshot.
    ``in_flight`` holds pool-submitted (job_id, node_key) pairs until the
    pool closure removes them, blocking duplicate bundle builds.
    """

    active_nodes: set[tuple[str, str]] = field(default_factory=set)
    pool_full: bool = False
    stock_gated: int = 0
    stock_snapshot: StockSnapshot | None = None
    stock_loaded_at: float = 0.0
    stock_enqueued: dict[tuple[str, str], int] = field(default_factory=dict)
    in_flight: set[tuple[str, str]] = field(default_factory=set)

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
    if dispatch is None or not published_agent_definitions(worker.settings.database_url):
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

    Rough zero-DB filter on the route cache: cached non-agent routes are
    excluded; uncached candidates stay, so the result is a superset.
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
    if (job_id, node_key) in state.active_nodes or (job_id, node_key) in state.in_flight:
        return False
    snapshot = state.stock_snapshot
    if snapshot is not None and not snapshot.allows(
        workspace_id, agent_id, extra=state.stock_enqueued.get((workspace_id, agent_id), 0)
    ):
        state.stock_gated += 1
        return False
    return True
