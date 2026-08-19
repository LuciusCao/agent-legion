"""Per-kind claim windows for the Agent claim transaction (issue #125).

Split out of ``claim.py`` for the file-size budget. The claim scan used to
walk one cross-kind FIFO window: a flood of queued code requests ahead of
an agent request burned the whole MAX_CLAIM_ATTEMPTS budget on
code_capacity_full skips and starved the agent pool (2026-08-18 prod
incident). Each kind now scans its own SCAN_ROUNDS ladder with its own
attempt budget, and the leading kind alternates per claim pass via the
broker fairness counter so neither kind is systemically first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.agent_broker.claim_evaluate import evaluate_candidate
from server.app.agent_broker.claim_scan import (
    MAX_CLAIM_ATTEMPTS,
    SCAN_ROUNDS,
    AgentClaim,
    ScanState,
    WorkerView,
    fair_candidate_order,
    fetch_candidates,
    window_saturated,
)
from server.app.agent_workers import CODE_PROTOCOL_VERSION

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker


def needed_claim_kinds(view: WorkerView) -> list[str]:
    """Kinds this Worker can still claim, in canonical (agent, code) order.

    A kind is needed when its capacity pool has headroom; code additionally
    requires protocol v2 (a v1 Worker must never hold kind='code'
    executions, so scanning for them would only produce skips).
    """
    kinds = []
    if view.agent_active < view.agent_capacity:
        kinds.append("agent")
    if view.code_active < view.code_capacity and view.protocol_version >= CODE_PROTOCOL_VERSION:
        kinds.append("code")
    return kinds


def scan_kind(
    broker: AgentExecutionBroker,
    conn: Any,
    worker_id: str,
    view: WorkerView,
    state: ScanState,
    kind: str,
    cursor: int,
) -> AgentClaim | None:
    """Run the bounded SCAN_ROUNDS ladder for one kind.

    ``state.attempts`` is the per-kind budget — the caller resets it before
    each kind so an unclaimable flood in one kind never consumes the other's
    attempts; ``state.skip_reasons`` keeps accumulating across kinds.
    """
    for per_workspace, window in SCAN_ROUNDS:
        candidates = fetch_candidates(conn, per_workspace, window, kind)
        if not candidates:
            break
        for selected in fair_candidate_order(candidates, cursor):
            if state.attempts >= MAX_CLAIM_ATTEMPTS:
                break
            claimed = evaluate_candidate(broker, conn, worker_id, selected, view, state)
            if claimed is not None:
                return claimed
        if state.attempts >= MAX_CLAIM_ATTEMPTS or not window_saturated(
            candidates, per_workspace, window
        ):
            break
    return None
