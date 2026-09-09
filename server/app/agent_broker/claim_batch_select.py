"""Read-only candidate selection for the batch claim (#555 phase 1).

#546 ran the scan ladder INSIDE the batch write transaction: every promoted
candidate's advisory xact locks (``agent-ws:*`` / ``agent-worker:*``) and
row locks (jobs / job_nodes / agent_execution_requests) were held to COMMIT
while the next candidate's ``fetch_candidates`` scan ran, stretching the
lock window to O(batch x scan). This module is the structural fix's first
half: the whole selection — scan ladder, admission filters, fairness
rotation, ascending-ws lock floor, pool budgets — runs on a read-only
connection that holds NO locks; ``claim_batch_tx`` then promotes the
selected rows in a compact write transaction.

The selection is deliberately optimistic and never trusted: between the
read and the write a candidate may be claimed by a competitor, cancelled,
or its job paused/failed. The write phase revalidates every candidate
through ``evaluate_candidate`` (SKIP LOCKED row probe, job re-check,
capacity gates, conditional promote) and treats staleness as skip/raced —
never half-applied. Worst case the batch comes back short and the Worker's
next poll refills.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from server.app.agent_broker import claim_timing as _claim_timing
from server.app.agent_broker.claim_admission import admit_candidate
from server.app.agent_broker.claim_scan import (
    SCAN_ROUNDS,
    ScanState,
    WorkerView,
    fair_candidate_order,
    fetch_candidates,
    window_saturated,
)
from server.app.agent_broker.claim_setup import ACTIVE_COUNT_SQL, build_worker_view
from server.app.agent_broker.claim_windows import needed_claim_kinds
from server.app.db.transaction import read_connection

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker

# Hard batch ceiling (same discipline as MAX_BATCH_HEARTBEATS, #352): one
# batch = one write transaction, and an unbounded batch would stretch the
# Worker row lock into a long-transaction problem. One promote is a fixed
# handful of primary-key writes, so 256 keeps the worst case in the
# tens-of-milliseconds range while covering every realistic batch the Worker
# side can ask for (worker claim_batch_limit caps at the same value).
MAX_BATCH_CLAIMS = 256

# Unlocked twin of claim_setup.WORKER_SELECT_SQL: the read phase must not
# take the worker row lock (that is exactly the lock window being shrunk).
_WORKER_READ_SQL = "select * from agent_workers where worker_id=%s"


@dataclass(frozen=True)
class BatchClaimSelection:
    """Read-phase output carried into the write phase.

    ``candidates`` are scan rows in promote order (ws-ascending for agent
    kinds, fairness-rotated across workspaces); ``timer`` carries the scan/
    evaluate stage timings so the write phase reports one coherent claim
    profile (#448) across both phases.
    """

    candidates: tuple[Mapping[str, Any], ...]
    skip_reasons: dict[str, int]
    scan_skipped: bool
    timer: _claim_timing.ClaimStageTimer


def _read_claim_view(
    conn: Any,
    worker_id: str,
    declared_max_concurrency: int | None,
    declared_max_code_concurrency: int | None,
    timer: _claim_timing.ClaimStageTimer,
) -> WorkerView:
    """Build the WorkerView from an UNLOCKED worker row read.

    Declared capacities override the registered ones exactly like
    ``sync_declared_capacity`` computes, but nothing is written here — the
    write phase's ``prepare_claim_view`` re-reads under the row lock, syncs
    the declared values, and its view is the authoritative one for every
    per-candidate capacity gate.
    """
    worker = conn.execute(_WORKER_READ_SQL, (worker_id,)).fetchone()
    timer.stage("worker_setup")
    if worker is None or worker["revoked_at"] is not None:
        raise ValueError("unknown or revoked Agent Worker")
    agent_pool = (
        declared_max_concurrency
        if declared_max_concurrency is not None
        else int(worker["max_concurrency"])
    )
    code_pool = (
        declared_max_code_concurrency
        if declared_max_code_concurrency is not None
        else int(worker["max_code_concurrency"])
    )
    active_rows = conn.execute(ACTIVE_COUNT_SQL, (worker_id,)).fetchall()
    timer.stage("worker_setup")
    return build_worker_view(worker, agent_pool, code_pool, active_rows)


def _select_kind_batch(
    conn: Any,
    broker: AgentExecutionBroker,
    view: WorkerView,
    state: ScanState,
    kind: str,
    cursor: int,
    timer: _claim_timing.ClaimStageTimer,
    ws_lock_floor: str | None,
    chosen_ids: set[str],
) -> Mapping[str, Any] | None:
    """``claim_windows.scan_kind`` 的批选择形态：同一 SCAN_ROUNDS 阶梯、fair
    轮转与批专属 ``ws_lock_floor`` 过滤，但「评估」只做锁前准入
    （``admit_candidate``）——无任何锁与写，可在只读连接上跑。

    两条扫描路径的阶梯/预算规则若改动必须同步（批测试钉住等价语义）。
    per-kind 尝试预算（MAX_CLAIM_ATTEMPTS）在此不适用：预算限的是准入后
    的锁竞争失败，而锁只在写入段出现——选择段每个准入候选即选中返回。
    ``chosen_ids`` 去重：选择段不写库，下一槽位的重扫会再次看到已选行。
    """
    for per_workspace, window in SCAN_ROUNDS:
        candidates = fetch_candidates(conn, per_workspace, window, kind)
        timer.stage("scan")
        if not candidates:
            break
        for row in fair_candidate_order(candidates, cursor):
            if str(row["execution_id"]) in chosen_ids:
                continue
            if (
                ws_lock_floor is not None
                and kind != "code"
                and str(row["workspace_id"]) < ws_lock_floor
            ):
                # 批内 ws 锁升序（EXEC-CLAIM-LOCK-001）：低序候选让位下一批
                # （floor 重置），本批绝不下探。选择段的顺序即写入段的加锁
                # 顺序，floor 纪律由这里守住。
                state.skip_reasons["batch_lock_order"] += 1
                continue
            if admit_candidate(broker, row, view, state) is not None:
                return row
        timer.stage("evaluate")
        if not window_saturated(candidates, per_workspace, window):
            break
    return None


def select_batch_candidates(
    broker: AgentExecutionBroker,
    worker_id: str,
    declared_max_concurrency: int | None = None,
    declared_max_code_concurrency: int | None = None,
    *,
    limit: int,
    agent_limit: int | None = None,
    code_limit: int | None = None,
) -> BatchClaimSelection:
    """Select up to ``min(limit, MAX_BATCH_CLAIMS)`` candidates, lock-free.

    Mirrors the #546 in-transaction loop's fairness (one cursor advance per
    slot, alternating leading kind), pool budgets and mid-batch capacity
    accounting — the view is the read-phase snapshot and must account itself
    per selection or the batch could select past the Worker's declared
    pools. Both-pools-exhausted short-circuits as ``scan_skipped`` (same
    verdict the write phase would have made).
    """
    timer = _claim_timing.ClaimStageTimer()
    with read_connection(broker.database_dsn) as conn:
        view = _read_claim_view(
            conn, worker_id, declared_max_concurrency, declared_max_code_concurrency, timer
        )
        if not needed_claim_kinds(view):
            # Both pools exhausted (or code-only headroom on a pre-v2 Worker):
            # same scan-skipped short-circuit as the single claim.
            return BatchClaimSelection((), {}, scan_skipped=True, timer=timer)
        budget = min(limit, MAX_BATCH_CLAIMS)
        pool_remaining = {"agent": agent_limit, "code": code_limit}
        selected: list[Mapping[str, Any]] = []
        chosen_ids: set[str] = set()
        state = ScanState()
        # Ascending-workspace lock floor (codex P1 / EXEC-CLAIM-LOCK-001):
        # the write phase accumulates one ``agent-ws:*`` advisory lock per
        # claimed workspace; without an ascending constraint two batches
        # walking the same workspaces in different queue orders could AB-BA
        # deadlock. Agent candidates below the floor are deferred (skip +
        # next batch = fresh floor); code claims take no ws lock and stay
        # unconstrained.
        ws_lock_floor: str | None = None
        while len(selected) < budget:
            open_kinds = []
            for kind in needed_claim_kinds(view):
                pool_left = pool_remaining[kind]
                if pool_left is None or pool_left > 0:
                    open_kinds.append(kind)
            if not open_kinds:
                break
            cursor = next(broker._fairness_counter)
            # Alternate the leading kind per slot, as the single path does
            # per pass, so neither kind is systemically first behind the
            # other's flood.
            if cursor % 2:
                open_kinds.reverse()
            row: Mapping[str, Any] | None = None
            for kind in open_kinds:
                row = _select_kind_batch(
                    conn, broker, view, state, kind, cursor, timer, ws_lock_floor, chosen_ids
                )
                if row is not None:
                    break
            if row is None:
                break
            selected.append(row)
            chosen_ids.add(str(row["execution_id"]))
            row_kind = str(row["kind"])
            row_workspace = str(row["workspace_id"])
            if row_kind == "agent" and (ws_lock_floor is None or row_workspace > ws_lock_floor):
                ws_lock_floor = row_workspace
            pool_left = pool_remaining[row_kind]
            if pool_left is not None:
                pool_remaining[row_kind] = pool_left - 1
            view = dataclasses.replace(
                view,
                agent_active=view.agent_active + (1 if row_kind == "agent" else 0),
                code_active=view.code_active + (1 if row_kind == "code" else 0),
            )
    return BatchClaimSelection(
        tuple(selected), dict(state.skip_reasons), scan_skipped=False, timer=timer
    )
