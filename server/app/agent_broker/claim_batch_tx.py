"""Batch claim write phase (issue #546's promote loop; #555 two-phase split).

Split from ``claim_batch.py`` for the file budget (the orchestrator with its
post-commit discipline lives there). Since #555 the batch is two-phase: the
scan ladder + admission run on a read-only connection in
``claim_batch_select.py`` (holding no locks), and this module owns only the
compact write transaction — the per-candidate SAVEPOINT containment and the
loop that promotes the selection. The write phase therefore holds its
advisory xact locks (``agent-ws:*`` / ``agent-worker:*``) and row locks
(jobs / job_nodes / agent_execution_requests) for O(writes), never across a
scan.

Revalidation discipline (#555): the selection is read-phase optimistic, so
every candidate re-runs ``evaluate_candidate`` here — the SKIP LOCKED row
probe, job control re-check, capacity gates and conditional promote all
execute against write-time state. A candidate that left the runnable set
since selection skips (stale) or, when the exit lands mid-promote, rolls
its own savepoint back (``ClaimRacedError``); nothing is half-applied.

Lock order (EXEC-CLAIM-LOCK-001): the selection's ascending-workspace floor
(``claim_batch_select``) fixes the promote order, so this loop takes the
batch's ws locks in ascending workspace order without re-checking — two
concurrent batches share one global lock order and cannot AB-BA.

Partial-failure semantics (mirroring the #352 batch-heartbeat pattern): each
promote attempt rides a SAVEPOINT, so a mid-batch ``ClaimRacedError`` rolls
back ONLY that candidate and ends the batch with the first k claims kept —
never the whole transaction. All other candidate-level conflicts
(``capacity_raced`` / shard dedup / ``lock_raced`` …) are skip-and-continue
inside ``evaluate_candidate`` and behave identically here.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from server.app.agent_broker import claim_timing as _claim_timing
from server.app.agent_broker.claim import report_claim_stages
from server.app.agent_broker.claim_batch_select import BatchClaimSelection
from server.app.agent_broker.claim_evaluate import evaluate_candidate
from server.app.agent_broker.claim_scan import (
    AgentClaim,
    ClaimRacedError,
    ScanState,
    WorkerView,
)
from server.app.agent_broker.claim_setup import prepare_claim_view
from server.app.agent_broker.worker_presence import touch_worker

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker


@dataclass(frozen=True)
class BatchClaimOutcome:
    """Post-transaction snapshot of one batch claim pass.

    Same #498 discipline as the single claim's ``ClaimOutcome``: the caller
    emits events only AFTER the write transaction commits, from this frozen
    snapshot. ``view`` carries the FINAL active counts (incremented per
    promote in the loop) so the post-commit events observe the batch's end
    state, and ``scan_skipped`` preserves the both-pools-full short-circuit.
    """

    claims: tuple[AgentClaim, ...]
    view: WorkerView
    skip_reasons: dict[str, int]
    scan_skipped: bool = False


def _promote_selected(
    broker: AgentExecutionBroker,
    conn: Any,
    worker_id: str,
    selected: Any,
    view: WorkerView,
    state: ScanState,
    timer: _claim_timing.ClaimStageTimer,
) -> tuple[AgentClaim | None, bool]:
    """One savepoint-guarded promote attempt for a selected candidate.

    Returns ``(claim, raced)``; ``raced`` = a ClaimRacedError rolled the
    attempt back to the savepoint and the batch must stop with what it has.
    The stop is not just "the partial batch is already a good answer":
    ``pg_advisory_xact_lock`` is NOT released by ROLLBACK TO SAVEPOINT, so a
    raced candidate's ws/worker advisory locks stay held to COMMIT —
    continuing past a raced candidate would stretch the lock window this
    two-phase split exists to shrink (and walk further down a queue whose
    head just proved unstable). Do not turn the ``break`` into ``continue``.
    A None claim without ``raced`` is a stale candidate (lost the SKIP
    LOCKED probe, paused, capacity filled since selection): skip and let the
    loop take the next selected row.
    """
    conn.execute("savepoint claim_batch_item")
    try:
        claimed = evaluate_candidate(broker, conn, worker_id, selected, view, state, timer)
    except ClaimRacedError:
        conn.execute("rollback to savepoint claim_batch_item")
        conn.execute("release savepoint claim_batch_item")
        return None, True
    conn.execute("release savepoint claim_batch_item")
    return claimed, False


def claim_batch_in_transaction(
    broker: AgentExecutionBroker,
    conn: Any,
    worker_id: str,
    declared_max_concurrency: int | None = None,
    declared_max_code_concurrency: int | None = None,
    *,
    selection: BatchClaimSelection,
) -> BatchClaimOutcome:
    """Promote the read-phase selection inside the caller's write transaction.

    ``prepare_claim_view`` re-reads the Worker row under its lock and syncs
    declared capacities, so its pool snapshot — not the read phase's — gates
    every promote; the loop still accounts each promote into the view so the
    batch cannot overrun the Worker's declared pools. Skip reasons merge the
    read phase's admission histogram with the write phase's race skips so
    the empty-claim signal keeps both halves (#448/#461 instrumentation).
    """
    timer = selection.timer
    view = prepare_claim_view(
        conn, worker_id, declared_max_concurrency, declared_max_code_concurrency, timer
    )
    if selection.scan_skipped:
        # Both pools exhausted (or code-only headroom on a pre-v2 Worker) at
        # selection time: the write phase still syncs declared capacities and
        # touches presence, same as the pre-#555 in-transaction short-circuit.
        touch_worker(conn, worker_id, min_interval_seconds=broker.touch_worker_interval_seconds)
        timer.stage("writes")
        report_claim_stages(timer, worker_id, claimed=False, state=ScanState())
        return BatchClaimOutcome((), view, {}, scan_skipped=True)
    claims: list[AgentClaim] = []
    state = ScanState()
    for candidate in selection.candidates:
        claimed, raced = _promote_selected(broker, conn, worker_id, candidate, view, state, timer)
        if raced:
            break
        if claimed is None:
            continue
        claims.append(claimed)
        # The capacity view was snapshotted at the top of the transaction;
        # every promote must account itself or the batch could overrun the
        # Worker's declared pools.
        view = dataclasses.replace(
            view,
            agent_active=view.agent_active + (1 if claimed.kind == "agent" else 0),
            code_active=view.code_active + (1 if claimed.kind == "code" else 0),
        )
    touch_worker(conn, worker_id, min_interval_seconds=broker.touch_worker_interval_seconds)
    timer.stage("writes")
    report_claim_stages(timer, worker_id, claimed=bool(claims), state=state)
    skip_reasons = dict(selection.skip_reasons)
    for reason, count in state.skip_reasons.items():
        skip_reasons[reason] = skip_reasons.get(reason, 0) + count
    return BatchClaimOutcome(tuple(claims), view, skip_reasons)
