"""Batch claim pass (issue #546): how much one claim round-trip asks for, and
the per-pass claim loop itself.

Pure sizing helpers plus the hot-reloadable config loader and the inner claim
loop, same family as ``claim_budget.py`` / ``claim_pacing.py``: the executor
folds ``pass_budget``'s per-pool budgets into one batch request's
``(limit, agent_limit, code_limit)`` — the per-pool caps let an instantaneous
code flood fill the code pool in one beat without touching agent slots, and
every clamp that feeds the budget (ramp-up #471, upload-backlog decay,
cross-pool suppression #534) therefore applies to the batch size unchanged.
``claim.attempt`` moved here from ``worker.events`` with its only call site
(the event name stays registered there).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from worker import events
from worker.runtime.controls import load_config

# Defaults: 32 lifts the refill ceiling from ~350/min to thousands/min at a
# ~100ms RTT while keeping one Host transaction in the tens of milliseconds;
# 256 matches the Host-side hard cap
# (agent_broker.claim_batch_select.MAX_BATCH_CLAIMS; moved by #555's
# two-phase split).
DEFAULT_CLAIM_BATCH_LIMIT = 32
MAX_CLAIM_BATCH_LIMIT = 256


def load_claim_batch_limit(path: Path) -> int:
    """Hot-read ``claim_batch_limit`` (absent = DEFAULT_CLAIM_BATCH_LIMIT).

    Invalid values raise ValueError — a startup fail-fast (config error, not
    a transient) and the hot-reload keeps the previous value, the same
    contract as ``runtime.controls.load_claim_controls``.
    """
    value = load_config(path).get("claim_batch_limit", DEFAULT_CLAIM_BATCH_LIMIT)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_CLAIM_BATCH_LIMIT
    ):
        raise ValueError(f"claim_batch_limit 必须是 1 到 {MAX_CLAIM_BATCH_LIMIT} 的整数")
    return value


def batch_request(budget: dict[str, int], batch_limit: int) -> tuple[int, int, int]:
    """Fold the per-pool budget into ``(limit, agent_limit, code_limit)``.

    Each pool asks for ``min(pool budget, batch_limit)``; the total caps at
    ``min(sum, batch_limit)``. A negative pool budget (cross-pool over-claim,
    #534) contributes zero — the Host must stop serving that pool THIS pass,
    not one pass later.
    """
    agent = min(max(budget["agent"], 0), batch_limit)
    code = min(max(budget["code"], 0), batch_limit)
    return min(agent + code, batch_limit), agent, code


def note_claim_attempt(
    worker_id: str, budget: dict[str, int], upload_backlog: int, claim_enabled: bool, limit: int
) -> None:
    """claim.attempt: one claim poll's local budget snapshot (idle rhythm =
    one per poll_interval; a single JSON line the Host-side
    claim.granted/empty/rejected events align against by worker_id).
    ``limit`` (#546) is the batch size this pass asks for."""
    events.emit_event(
        "claim.attempt",
        {
            "worker_id": worker_id,
            "agent_budget": budget["agent"],
            "code_budget": budget["code"],
            "upload_backlog": upload_backlog,
            "claim_enabled": claim_enabled,
            "limit": limit,
        },
    )


@dataclass
class ClaimRunContext:
    """Loop-invariant claim-loop wiring (executor main scope, built once).

    ``active`` / ``active_kinds`` / ``pool_deferred`` are mutated in place by
    the loop (identity-stable), so they belong on the context; ``budget`` /
    ``declared`` are rebuilt per pass and ride the call instead.
    """

    client: Any
    worker_id: str
    pool: Any
    run_args: tuple[Any, ...]
    run_tail: tuple[Any, ...]
    heartbeat_registry: Any
    active: set[Any]
    active_kinds: dict[Any, str]
    pool_deferred: set[str]
    stop: Any


def make_claim_submitter(
    ctx: ClaimRunContext, budget: dict[str, int]
) -> Callable[[dict[str, Any]], None]:
    """Build the per-claim submit closure for one pass.

    Lives here, not inline in the executor loop body: a closure defined in a
    loop body trips B023 on every captured mutable container, and these are
    all per-pass containers the closure must capture by identity.
    """
    from worker.execution.run import run_execution

    def submit_claim(claim: dict[str, Any]) -> None:
        kind = "code" if str(claim.get("kind")) == "code" else "agent"
        events.note_claim_received(ctx.worker_id, claim)
        # Host 已在 claim 事务强制分池；竞态超发照单收下（Host 记账）。
        budget[kind] -= 1
        # #352：heartbeat_registry 追加在 #471 的 run_args/run_tail 拆组之后
        # （registry 仍是 run_execution 的默认参数位）。
        future = ctx.pool.submit(
            run_execution, ctx.client, claim, *ctx.run_args, *ctx.run_tail, ctx.heartbeat_registry
        )
        ctx.active.add(future)
        ctx.active_kinds[future] = kind

    return submit_claim


def claim_batch_pass(
    ctx: ClaimRunContext,
    budget: dict[str, int],
    declared: dict[str, int],
    batch_limit: int,
    upload_depth: int,
    claim_enabled: bool,
    submit: Callable[[dict[str, Any]], None],
) -> tuple[bool, float]:
    """One batch claim round; returns (claimed_any, equivalent per-claim RTT).

    ``submit`` consumes each claimed execution — a delivered batch must be
    submitted in full, never dropped mid-batch (#535's hung-lease rule). The
    RTT is the batch round-trip divided by the batch size: the #472 pacing
    keeps its adaptive per-claim semantics on the equivalent single-claim
    latency.
    """
    limit, agent_limit, code_limit = batch_request(budget, batch_limit)
    note_claim_attempt(ctx.worker_id, budget, upload_depth, claim_enabled, limit)
    started = time.monotonic()
    # #501：声明的是目标容量而非爬坡档位（声明不随档位抖；#534 越池抑制期间
    # 该池声明压到当前活跃数，pass_budget 已折好 declared）。
    claims = ctx.client.claim_batch(
        ctx.worker_id,
        declared["agent"],
        declared["code"],
        limit=limit,
        agent_limit=agent_limit,
        code_limit=code_limit,
    )
    if not claims:
        return False, 0.0
    rtt = (time.monotonic() - started) / len(claims)
    for claim in claims:
        submit(claim)
    return True, rtt


def drain_budget(
    ctx: ClaimRunContext,
    budget: dict[str, int],
    declared: dict[str, int],
    batch_limit: int,
    upload_depth: int,
    claim_enabled: bool,
    submit: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[bool, float]:
    """The inner claim loop: batch rounds until the budget is spent or the
    queue drains; returns (claimed_any, last batch's equivalent RTT).

    #534：按池判定（or）——旧条件两池求和，agent 池被爬坡/容量/背压钳到 0
    时 agent 领取把 agent 预算扣成负值并借 code 预算继续循环（实测 -31），
    #471 爬坡门被完全绕过。Host 按 #501 声明的目标容量记账不拦，本地预算
    是唯一的门。``WorkerAuthError`` 与传输错误原样上抛（executor 的终态 /
    退避分支）。pre-#546 Host 忽略批字段返回单条（client 侧包成单元素列
    表），循环自动退化为逐条领取，混合舰队无撕裂。
    """
    claimed, claim_rtt = False, 0.0
    if submit is None:
        submit = make_claim_submitter(ctx, budget)
    while budget["agent"] > 0 or budget["code"] > 0:
        if ctx.stop.is_set():
            break
        got, claim_rtt = claim_batch_pass(
            ctx, budget, declared, batch_limit, upload_depth, claim_enabled, submit
        )
        claimed = claimed or got
        if not got:
            break
        # #534（codex P1 复审 + 二轮，#546 批形态）：越池抑制从批内 break
        # 改为批后记账——预算转负的池记入 pool_deferred（预算 0 + 声明压到
        # 活跃数，见 pass_budget），下一轮的 batch_request 把该池的申请钳
        # 到 0，本批即止血。< 0 而非 <= 0：正常领满（预算 1 → 领取 → 0）
        # 不是越池，不抑制——否则 ramp 满档窗口声明容量会跌到档位值并随补
        # 位振荡，违反 #501「声明不随档位抖」。
        over_claimed = False
        for kind in ("agent", "code"):
            if budget[kind] < 0:
                ctx.pool_deferred.add(kind)
                over_claimed = True
        if over_claimed:
            # 批后终止本 pass（#534 的 break 语义保留在提交之后）：越池说明
            # Host 的记账面与本 pass 的申请已脱节（竞态/过期视图），继续按
            # 旧预算折算批大小只会把负值越扣越深。
            break
    return claimed, claim_rtt
