"""Batch claim transaction core (issue #546): the promote loop itself.

Split from ``claim_batch.py`` for the file budget (the orchestrator with its
post-commit discipline lives there): this module owns the in-transaction
machinery — the batch scan ladder with the ascending-workspace lock
constraint, the per-candidate SAVEPOINT containment, and the loop that
promotes up to ``limit`` executions.

Lock-order discipline (EXEC-CLAIM-LOCK-001 extended to batches): one batch
transaction accumulates an ``agent-ws:*`` advisory lock per claimed
workspace, so agent claims are constrained to ASCENDING workspace order
(``ws_lock_floor``) — two concurrent batches then share one global lock
order and cannot AB-BA. Candidates below the floor are deferred to the next
batch (a fresh transaction with a fresh floor), never failed.

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

from server.app.agent_broker import claim_timing as _claim_timing
from server.app.agent_broker.agent_worker_capacity import touch_worker
from server.app.agent_broker.claim import report_claim_stages
from server.app.agent_broker.claim_evaluate import evaluate_candidate
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
from server.app.agent_broker.claim_setup import prepare_claim_view
from server.app.agent_broker.claim_windows import needed_claim_kinds

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker

# Hard batch ceiling (same discipline as MAX_BATCH_HEARTBEATS, #352): one
# batch = one write transaction, and an unbounded batch would stretch the
# Worker row lock into a long-transaction problem. One promote is a fixed
# handful of primary-key writes, so 256 keeps the worst case in the
# tens-of-milliseconds range while covering every realistic batch the Worker
# side can ask for (worker claim_batch_limit caps at the same value).
MAX_BATCH_CLAIMS = 256


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


def _scan_kind_batch(
    broker: AgentExecutionBroker,
    conn: Any,
    worker_id: str,
    view: WorkerView,
    state: ScanState,
    kind: str,
    cursor: int,
    timer: _claim_timing.ClaimStageTimer,
    ws_lock_floor: str | None,
) -> AgentClaim | None:
    """``claim_windows.scan_kind`` 的批形态：同一 SCAN_ROUNDS 阶梯、fair
    轮转与 per-kind 尝试预算，外加批专属的 ``ws_lock_floor`` 过滤。

    不复用 scan_kind 加参数：claim_windows/claim_evaluate 都在预算上限
    （ceiling 只降不升），批专属约束留批模块。两条扫描路径的阶梯/预算
    规则若改动必须同步（批测试钉住等价语义）；floor 过滤置于
    evaluate_candidate 之前——让位不烧 per-kind 尝试预算。
    """
    for per_workspace, window in SCAN_ROUNDS:
        candidates = fetch_candidates(conn, per_workspace, window, kind)
        if timer is not None:
            timer.stage("scan")
        if not candidates:
            break
        for selected in fair_candidate_order(candidates, cursor):
            if state.attempts >= MAX_CLAIM_ATTEMPTS:
                break
            if (
                ws_lock_floor is not None
                and kind != "code"
                and str(selected["workspace_id"]) < ws_lock_floor
            ):
                # 批内 ws 锁升序（EXEC-CLAIM-LOCK-001）：低序候选让位下一批
                # （新事务、floor 重置），本批绝不下探。
                state.skip_reasons["batch_lock_order"] += 1
                continue
            claimed = evaluate_candidate(broker, conn, worker_id, selected, view, state, timer)
            if claimed is not None:
                return claimed
        if timer is not None:
            timer.stage("evaluate")
        if state.attempts >= MAX_CLAIM_ATTEMPTS or not window_saturated(
            candidates, per_workspace, window
        ):
            break
    return None


def _claim_one_kind(
    broker: AgentExecutionBroker,
    conn: Any,
    worker_id: str,
    view: WorkerView,
    state: ScanState,
    kinds: list[str],
    cursor: int,
    timer: _claim_timing.ClaimStageTimer,
    ws_lock_floor: str | None,
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
            claimed = _scan_kind_batch(
                broker, conn, worker_id, view, state, kind, cursor, timer, ws_lock_floor
            )
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
    # Ascending-workspace lock floor (codex P1 / EXEC-CLAIM-LOCK-001): one
    # batch transaction accumulates an ``agent-ws:*`` advisory lock per
    # claimed workspace; without an ascending constraint two batches walking
    # the same workspaces in different queue orders could AB-BA deadlock.
    # Agent claims below the floor are deferred (skip + next batch = fresh
    # transaction); code claims take no ws lock and stay unconstrained.
    ws_lock_floor: str | None = None
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
            broker, conn, worker_id, view, state, open_kinds, cursor, timer, ws_lock_floor
        )
        if raced or claimed is None:
            break
        claims.append(claimed)
        if claimed.kind == "agent" and (
            ws_lock_floor is None or claimed.workspace_id > ws_lock_floor
        ):
            ws_lock_floor = claimed.workspace_id
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
