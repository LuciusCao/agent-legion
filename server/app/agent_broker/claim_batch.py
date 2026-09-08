"""Batch claim orchestration (issue #546): transaction + retry + post-commit
side effects.

The in-transaction promote loop (scan ladder, savepoint containment,
ascending-workspace lock floor) lives in ``claim_batch_tx.py``; this module
owns the deadlock-retry policy and the committed-batch side effects,
mirroring ``broker.claim``'s discipline one-for-one.

Why batch at all: the serial claim loop's physical ceiling (~250-400
claims/min: one HTTP RTT plus pacing per claim, #472) cannot refill the
slots a completion wave releases instantly; a fleet of instantaneous code
nodes makes it worse — every 0-second execution still spends one full loop
beat on its claim. A batch claim amortizes the round-trip: the Worker asks
once for up to ``limit`` executions (per-pool via ``agent_limit`` /
``code_limit``) and the Host promotes them in a single transaction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from psycopg import Error

from server.app.agent_broker.claim_batch_tx import (
    BatchClaimOutcome,
    claim_batch_in_transaction,
)
from server.app.agent_broker.claim_scan import AgentClaim
from server.app.db.transaction import write_transaction

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker

__all__ = ["claim_batch", "claim_batch_with_retry"]

# One immediate retry on SQLSTATE 40P01, the exact policy of the single
# claim (claim_retry._CLAIM_DEADLOCK_RETRIES); the two modules stay parallel
# deliberately — see that module's docstring for why the policy is not the
# generic backoff helper.
_BATCH_DEADLOCK_RETRIES = 1


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
        # 批形态补标（#546）：_notify_worker_poll 只把最后一个 claim 的
        # workspace 置忙；批内其余 workspace 同样持有在跑执行，面板逐补。
        if broker.agent_status is not None:
            marked = claims[-1].workspace_id if claims else None
            for workspace_id in {claim.workspace_id for claim in claims} - {marked}:
                broker.agent_status.set_busy(worker_id, workspace_id=workspace_id)
        return claims
    finally:
        profile.note_claim(timer.stop(), empty=not claims)
