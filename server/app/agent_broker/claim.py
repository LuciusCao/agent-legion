"""Atomic claim transaction for the Agent execution queue.

Split out of ``broker.py`` so the broker module only carries the queue
protocol; mirrors the ``executors/_lease_*.py`` layout. The candidate window
scan lives in ``claim_scan.py`` for the file-size budget; this module keeps
the Worker-level setup and the bounded scan-round loop. Functions take the
broker instance as their first argument and must run inside the caller's
transaction unless noted otherwise.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING, Any

from server.app.agent_broker import agent_claim_compatibility
from server.app.agent_broker.agent_worker_capacity import sync_declared_capacity, touch_worker
from server.app.agent_broker.claim_evaluate import cancel_request, evaluate_candidate
from server.app.agent_broker.claim_scan import (
    MAX_CLAIM_ATTEMPTS,
    SCAN_ROUNDS,
    AgentClaim,
    ClaimRacedError,
    ScanState,
    WorkerView,
    fair_candidate_order,
    fetch_candidates,
    window_saturated,
)

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker

# Re-exports: the public claim surface stays importable from this module.
__all__ = [
    "AgentClaim",
    "ClaimRacedError",
    "cancel_request",
    "claim_in_transaction",
]


def claim_in_transaction(
    broker: AgentExecutionBroker,
    conn: Any,
    worker_id: str,
    declared_max_concurrency: int | None = None,
    declared_max_code_concurrency: int | None = None,
) -> tuple[AgentClaim | None, Counter[str]]:
    """Claim at most one request; also report why skipped candidates lost.

    The skip-reason counter separates "queue truly empty" from "queue head
    blocked by unclaimable requests" for the empty-claim signal (see
    ``empty.py``); it accumulates across every scan round.
    """
    worker = conn.execute(
        "select * from agent_workers where worker_id=%s for update", (worker_id,)
    ).fetchone()
    if worker is None or worker["revoked_at"] is not None:
        raise ValueError("unknown or revoked Agent Worker")
    max_concurrency, max_code_concurrency = sync_declared_capacity(
        conn, worker, declared_max_concurrency, declared_max_code_concurrency
    )
    capabilities, models = agent_claim_compatibility.worker_declarations(worker)
    active_rows = conn.execute(
        "select kind, count(*) as cnt from agent_execution_requests"
        " where worker_id=%s and state='claimed' group by kind",
        (worker_id,),
    ).fetchall()
    active_by_kind = {str(row["kind"]): int(row["cnt"]) for row in active_rows}
    agent_active = active_by_kind.get("agent", 0)
    code_active = active_by_kind.get("code", 0)
    view = WorkerView(
        runtimes=set(json.loads(worker["runtimes_json"])),
        capabilities=capabilities,
        models=models,
        labels=json.loads(worker["labels_json"]),
        allowed_workspaces=set(json.loads(worker["allowed_workspaces_json"] or "[]")),
        agent_capacity=max_concurrency,
        agent_active=agent_active,
        code_capacity=max_code_concurrency,
        code_active=code_active,
        protocol_version=int(worker["protocol_version"]),
    )
    # Both pools exhausted: nothing this Worker could claim, skip the scan.
    if agent_active >= max_concurrency and code_active >= max_code_concurrency:
        touch_worker(conn, worker_id)
        return None, Counter()
    cursor = next(broker._fairness_counter)
    state = ScanState()
    for per_workspace, window in SCAN_ROUNDS:
        candidates = fetch_candidates(conn, per_workspace, window)
        if not candidates:
            break
        for selected in fair_candidate_order(candidates, cursor):
            if state.attempts >= MAX_CLAIM_ATTEMPTS:
                break
            claimed = evaluate_candidate(broker, conn, worker_id, selected, view, state)
            if claimed is not None:
                touch_worker(conn, worker_id)
                return claimed, state.skip_reasons
        if state.attempts >= MAX_CLAIM_ATTEMPTS or not window_saturated(
            candidates, per_workspace, window
        ):
            break
    touch_worker(conn, worker_id)
    return None, state.skip_reasons
