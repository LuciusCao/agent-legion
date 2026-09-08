"""Batch claim transaction (issue #546): promote up to ``limit`` executions
for one Worker in ONE write transaction.

The serial claim loop's physical ceiling (~250-400 claims/min: one HTTP RTT
plus pacing per claim, #472) cannot refill the slots a completion wave
releases instantly; a fleet of instantaneous code nodes makes it worse —
every 0-second execution still spends one full loop beat on its claim. A
batch claim amortizes the round-trip: the Worker asks once for up to
``limit`` executions (per-pool via ``agent_limit`` / ``code_limit``) and the
Host promotes them in a single transaction, reusing the single claim's scan
ladder, fairness cursor, per-kind attempt budgets and skip semantics
verbatim (``claim_windows.scan_kind`` / ``claim_evaluate.evaluate_candidate``).

Partial-failure semantics (mirroring the #352 batch-heartbeat pattern): each
promote attempt rides a SAVEPOINT, so a mid-batch ``ClaimRacedError`` (the
job left the runnable set between the row lock and the jobs promote) rolls
back ONLY that candidate and ends the batch with the first k claims kept —
never the whole transaction. All other candidate-level conflicts
(``capacity_raced`` / shard dedup / ``lock_raced`` …) were already
skip-and-continue inside ``evaluate_candidate`` and behave identically here.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from psycopg import Error

from server.app.agent_broker import claim_timing as _claim_timing
from server.app.agent_broker.agent_worker_capacity import touch_worker
from server.app.agent_broker.claim import report_claim_stages
from server.app.agent_broker.claim_scan import (
    AgentClaim,
    ClaimRacedError,
    ScanState,
    WorkerView,
)
from server.app.agent_broker.claim_setup import prepare_claim_view
from server.app.agent_broker.claim_windows import needed_claim_kinds, scan_kind
from server.app.db.transaction import write_transaction

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker

# Hard batch ceiling (same discipline as MAX_BATCH_HEARTBEATS, #352): one
# batch = one write transaction, and an unbounded batch would stretch the
# Worker row lock into a long-transaction problem. One promote is a fixed
# handful of primary-key writes, so 256 keeps the worst case in the
# tens-of-milliseconds range while covering every realistic batch the Worker
# side can ask for (worker claim_batch_limit caps at the same value).
MAX_BATCH_CLAIMS = 256

# One immediate retry on SQLSTATE 40P01, the exact policy of the single
# claim (claim_retry._CLAIM_DEADLOCK_RETRIES); the two modules stay parallel
# deliberately — see that module's docstring for why the policy is not the
# generic backoff helper.
_BATCH_DEADLOCK_RETRIES = 1


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


def _claim_one_kind(
    broker: AgentExecutionBroker,
    conn: Any,
    worker_id: str,
    view: WorkerView,
    state: ScanState,
    kinds: list[str],
    cursor: int,
    timer: _claim_timing.ClaimStageTimer,
) -> tuple[AgentClaim | None, bool]:
    """One savepoint-guarded promote attempt over the open kinds.

    Returns ``(claim, raced)``; ``raced`` = a ClaimRacedError rolled the
    attempt back to the savepoint and the batch must stop with what it has
    (the job left the runnable set mid-claim — rescanning would just hit the
    same raced row again, and the partial batch is already a good answer).
    """
    conn.execute("savepoint claim_batch_item")
    claimed: AgentClaim | None = None
    try:
        for kind in kinds:
            # Per-kind attempt budget (issue #125), same reset the single
            # claim applies between kinds.
            state.attempts = 0
            claimed = scan_kind(broker, conn, worker_id, view, state, kind, cursor, timer)
            if claimed is not None:
                break
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
    limit: int,
    agent_limit: int | None = None,
    code_limit: int | None = None,
) -> BatchClaimOutcome:
    """Promote up to ``limit`` requests inside the caller's write transaction.

    ``agent_limit`` / ``code_limit`` cap each pool independently (None = the
    capacity view alone decides); the global loop bound is
    ``min(limit, MAX_BATCH_CLAIMS)``. The fairness cursor advances once per
    promoted claim, so a batch of N rotates workspaces exactly like N
    sequential single claims did.
    """
    timer = _claim_timing.ClaimStageTimer()
    view = prepare_claim_view(
        conn, worker_id, declared_max_concurrency, declared_max_code_concurrency, timer
    )
    if not needed_claim_kinds(view):
        # Both pools exhausted (or code-only headroom on a pre-v2 Worker):
        # same scan-skipped short-circuit as the single claim.
        touch_worker(conn, worker_id)
        timer.stage("writes")
        report_claim_stages(timer, worker_id, claimed=False, state=ScanState())
        return BatchClaimOutcome((), view, {}, scan_skipped=True)
    budget = min(limit, MAX_BATCH_CLAIMS)
    pool_remaining = {"agent": agent_limit, "code": code_limit}
    claims: list[AgentClaim] = []
    state = ScanState()
    while len(claims) < budget:
        open_kinds = []
        for kind in needed_claim_kinds(view):
            pool_left = pool_remaining[kind]
            if pool_left is None or pool_left > 0:
                open_kinds.append(kind)
        if not open_kinds:
            break
        cursor = next(broker._fairness_counter)
        # Alternate the leading kind per claim, as the single path does per
        # pass, so neither kind is systemically first behind the other's flood.
        if cursor % 2:
            open_kinds.reverse()
        claimed, raced = _claim_one_kind(
            broker, conn, worker_id, view, state, open_kinds, cursor, timer
        )
        if raced or claimed is None:
            break
        claims.append(claimed)
        pool_left = pool_remaining[claimed.kind]
        if pool_left is not None:
            pool_remaining[claimed.kind] = pool_left - 1
        # The capacity view was snapshotted at the top of the transaction;
        # every promote must account itself or the batch could overrun the
        # Worker's declared pools.
        view = dataclasses.replace(
            view,
            agent_active=view.agent_active + (1 if claimed.kind == "agent" else 0),
            code_active=view.code_active + (1 if claimed.kind == "code" else 0),
        )
    touch_worker(conn, worker_id)
    timer.stage("writes")
    report_claim_stages(timer, worker_id, claimed=bool(claims), state=state)
    return BatchClaimOutcome(tuple(claims), view, dict(state.skip_reasons))


def claim_batch_with_retry(
    broker: AgentExecutionBroker,
    worker_id: str,
    declared_max_concurrency: int | None,
    declared_max_code_concurrency: int | None,
    *,
    limit: int,
    agent_limit: int | None = None,
    code_limit: int | None = None,
) -> BatchClaimOutcome:
    """Run the batch claim transaction, retrying one SQLSTATE 40P01.

    The retry re-enters on a clean connection with the whole batch
    re-evaluated (write_transaction rolled the deadlocked one back), same as
    the single claim's policy — a deadlock costs one batch, never a partial
    one.
    """
    for attempt in range(1 + _BATCH_DEADLOCK_RETRIES):
        try:
            with write_transaction(broker.database_dsn) as conn:
                return claim_batch_in_transaction(
                    broker,
                    conn,
                    worker_id,
                    declared_max_concurrency,
                    declared_max_code_concurrency,
                    limit=limit,
                    agent_limit=agent_limit,
                    code_limit=code_limit,
                )
        except Error as exc:
            if getattr(exc, "sqlstate", None) != "40P01" or attempt >= _BATCH_DEADLOCK_RETRIES:
                raise
    raise RuntimeError("unreachable: batch claim retry loop exhausted")


def claim_batch(
    broker: AgentExecutionBroker,
    worker_id: str,
    declared_max_concurrency: int | None,
    declared_max_code_concurrency: int | None,
    *,
    limit: int,
    agent_limit: int | None = None,
    code_limit: int | None = None,
) -> list[AgentClaim]:
    """Full batch claim pass: transaction + deadlock retry + post-commit
    side effects, mirroring ``broker.claim``'s discipline one-for-one —
    per-claim ``claim.granted`` events and per-job ``record_job_update``
    after the commit (#498/#490), the empty-claim restock signal only when
    nothing was claimed, and one runtime-profile sample per pass (#359).
    ``ClaimRacedError`` never reaches here (the savepoint contains it); a
    second 40P01 propagates to the route's 500 exactly like the single path.
    """
    from server.app.agent_broker.worker_events import note_claim_outcome
    from server.app.events.aggregator import record_job_update
    from server.app.services.runtime_profile import profile

    timer = profile.claim_timer()
    claims: list[AgentClaim] = []
    try:
        outcome = claim_batch_with_retry(
            broker,
            worker_id,
            declared_max_concurrency,
            declared_max_code_concurrency,
            limit=limit,
            agent_limit=agent_limit,
            code_limit=code_limit,
        )
        claims = list(outcome.claims)
        if not claims:
            # Demand signal, same trigger as the single path: a Worker found
            # no work (debounced restock / skip-reason histogram, see empty).
            broker.empty_claim.note_empty_claim(
                broker.database_dsn, skip_reasons=outcome.skip_reasons
            )
            note_claim_outcome(
                worker_id,
                None,
                outcome.view,
                outcome.skip_reasons,
                scan_skipped=outcome.scan_skipped,
            )
        else:
            for job_id in dict.fromkeys(claim.job_id for claim in claims):
                record_job_update(broker.job_db, broker.job_event_buffer, job_id)
            for claimed in claims:
                note_claim_outcome(worker_id, claimed, outcome.view, {})
        broker._notify_worker_poll(worker_id, claims[-1] if claims else None)
        return claims
    finally:
        profile.note_claim(timer.stop(), empty=not claims)
