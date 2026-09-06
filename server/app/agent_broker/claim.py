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
from typing import TYPE_CHECKING, Any

from server.app.agent_broker import agent_claim_compatibility
from server.app.agent_broker import claim_timing as _claim_timing
from server.app.agent_broker.agent_worker_capacity import sync_declared_capacity, touch_worker
from server.app.agent_broker.claim_scan import (
    AgentClaim,
    ClaimOutcome,
    ClaimRacedError,
    ScanState,
    WorkerView,
)
from server.app.agent_broker.claim_windows import needed_claim_kinds, scan_kind
from server.app.agent_broker.manifest_trim import cancel_request

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker

# Re-exports: the public claim surface stays importable from this module.
__all__ = [
    "AgentClaim",
    "ClaimOutcome",
    "ClaimRacedError",
    "cancel_request",
    "claim_in_transaction",
]

# Claim-stage accounting (#448 phase 1): the serial worker claim loop makes
# one claim's round-trip the throughput ceiling; the timer splits it into
# worker_setup / scan / evaluate / writes (commit deliberately unmeasured —
# it sits past this function's return).
_WORKER_SELECT_SQL = "select * from agent_workers where worker_id=%s for update"
_ACTIVE_COUNT_SQL = (
    "select kind, count(*) as cnt from agent_execution_requests"
    " where worker_id=%s and state='claimed' group by kind"
)


def claim_in_transaction(
    broker: AgentExecutionBroker,
    conn: Any,
    worker_id: str,
    declared_max_concurrency: int | None = None,
    declared_max_code_concurrency: int | None = None,
) -> ClaimOutcome:
    """Claim at most one request; the verdict rides out as ``ClaimOutcome``.

    The skip-reason counter separates "queue truly empty" from "queue head
    blocked by unclaimable requests" for the empty-claim signal (see
    ``empty.py``); it accumulates across every scan round. The #448 stage
    timer is best-effort instrumentation (dict stores, never raises).

    #498: no event emission here — the claim events describe the COMMITTED
    claim, so the broker emits them after ``write_transaction``'s commit
    (the ``record_job_update`` discipline) from the returned snapshot.
    ``ClaimRacedError`` propagates with no outcome: a raced discard was
    never a claim.
    """
    timer = _claim_timing.ClaimStageTimer()
    worker = conn.execute(_WORKER_SELECT_SQL, (worker_id,)).fetchone()
    timer.stage("worker_setup")
    if worker is None or worker["revoked_at"] is not None:
        raise ValueError("unknown or revoked Agent Worker")
    max_concurrency, max_code_concurrency = sync_declared_capacity(
        conn, worker, declared_max_concurrency, declared_max_code_concurrency
    )
    models = agent_claim_compatibility.worker_model_declarations(worker)
    active_rows = conn.execute(_ACTIVE_COUNT_SQL, (worker_id,)).fetchall()
    timer.stage("worker_setup")
    active_by_kind = {str(row["kind"]): int(row["cnt"]) for row in active_rows}
    view = WorkerView(
        runtimes=set(json.loads(worker["runtimes_json"])),
        models=models,
        labels=json.loads(worker["labels_json"]),
        allowed_workspaces=set(json.loads(worker["allowed_workspaces_json"] or "[]")),
        agent_capacity=max_concurrency,
        agent_active=active_by_kind.get("agent", 0),
        code_capacity=max_code_concurrency,
        code_active=active_by_kind.get("code", 0),
        protocol_version=int(worker["protocol_version"]),
    )
    # Nothing this Worker could claim (both pools exhausted, or only code
    # headroom on a pre-v2 Worker): skip the scan entirely.
    kinds = needed_claim_kinds(view)
    if not kinds:
        touch_worker(conn, worker_id)
        timer.stage("writes")
        _report_claim_stages(timer, worker_id, claimed=None, state=ScanState())
        return ClaimOutcome(None, view, {}, scan_skipped=True)
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
        claimed = scan_kind(broker, conn, worker_id, view, state, kind, cursor, timer)
        if claimed is not None:
            touch_worker(conn, worker_id)
            timer.stage("writes")
            _report_claim_stages(timer, worker_id, claimed=claimed, state=state)
            return ClaimOutcome(claimed, view, dict(state.skip_reasons))
    touch_worker(conn, worker_id)
    timer.stage("writes")
    _report_claim_stages(timer, worker_id, claimed=None, state=state)
    return ClaimOutcome(None, view, dict(state.skip_reasons))


def _report_claim_stages(
    timer: _claim_timing.ClaimStageTimer,
    worker_id: str,
    *,
    claimed: AgentClaim | None,
    state: ScanState,
) -> None:
    """Log + profile one claim attempt's stage timings (best-effort).

    Deliberately still called INSIDE the transaction (unlike the claim
    events, #498): the stage timer instruments the attempt, not a verdict
    about a committed claim, and its contract is best-effort never-raises —
    an attempt whose transaction later rolls back may still report stages."""
    _claim_timing.log_claim_stages(
        timer.stages,
        worker_id=worker_id,
        claimed=claimed is not None,
        attempts=state.attempts,
        skipped=sum(state.skip_reasons.values()),
    )
    _claim_timing.note_claim_stages(timer.stages)
