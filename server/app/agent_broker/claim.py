"""Atomic claim transaction for the Agent execution queue.

Split out of ``broker.py`` so the broker module only carries the queue
protocol; mirrors the ``executors/_lease_*.py`` layout. The candidate window
scan lives in ``claim_scan.py`` and the per-kind scan-round loop in
``claim_windows.py`` (file-size budget); this module keeps the Worker-level
setup and the per-kind orchestration. Functions take the broker instance as
their first argument and must run inside the caller's transaction unless
noted otherwise.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING, Any

from server.app.agent_broker import agent_claim_compatibility
from server.app.agent_broker.agent_worker_capacity import sync_declared_capacity, touch_worker
from server.app.agent_broker.claim_evaluate import cancel_request
from server.app.agent_broker.claim_scan import AgentClaim, ClaimRacedError, ScanState, WorkerView
from server.app.agent_broker.claim_windows import needed_claim_kinds, scan_kind

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
    # Nothing this Worker could claim (both pools exhausted, or only code
    # headroom on a pre-v2 Worker): skip the scan entirely.
    kinds = needed_claim_kinds(view)
    if not kinds:
        touch_worker(conn, worker_id)
        return None, Counter()
    cursor = next(broker._fairness_counter)
    # Alternate the leading kind per pass so neither kind is systemically
    # first behind the other kind's flood.
    if cursor % 2:
        kinds.reverse()
    state = ScanState()
    for kind in kinds:
        # Per-kind attempt budget (issue #125): an unclaimable flood in one
        # kind never consumes the other kind's attempts.
        state.attempts = 0
        claimed = scan_kind(broker, conn, worker_id, view, state, kind, cursor)
        if claimed is not None:
            touch_worker(conn, worker_id)
            return claimed, state.skip_reasons
    touch_worker(conn, worker_id)
    return None, state.skip_reasons
