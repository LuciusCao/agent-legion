"""Per-candidate claim evaluation for the Agent execution queue.

Split out of ``claim_scan.py`` for the file-size budget: given one candidate
row (from the bounded window scan, or from the #555 batch read phase) and
the Worker view, try to claim it — admission, the advisory-lock ladder, the
row lock, the job re-check + execution-generation CAS, capacity enforcement.
The lock-free admission filters live in
``claim_admission.py`` (#555 — shared with the batch read phase, which runs
them outside any lock window); the promote write sequence (run row / lease /
request flip / jobs promote / queue-wait gauge) lives in
``claim_promote.py`` (#551). Must run inside the caller's transaction.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from server.app.agent_broker import claim_node_limit
from server.app.agent_broker.claim_admission import admit_candidate
from server.app.agent_broker.claim_promote import promote_claim
from server.app.agent_broker.claim_scan import (
    RUNNABLE_JOB_STATUSES,
    AgentClaim,
    ScanState,
    WorkerView,
)
from server.app.agent_broker.manifest_trim import cancel_request
from server.app.workflows.sharding import try_start_shard

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker
    from server.app.agent_broker.claim_timing import ClaimStageTimer


def evaluate_candidate(
    broker: AgentExecutionBroker,
    conn: Any,
    worker_id: str,
    selected: Mapping[str, Any],
    view: WorkerView,
    state: ScanState,
    timer: ClaimStageTimer | None = None,
    batch_code_pool_lock: bool | None = None,
) -> AgentClaim | None:
    """Try to claim one candidate; on a skip, record the cause in state.skip_reasons.

    ``timer`` (#448) closes the promote-write sequence below into the
    "writes" stage — the lock/validate prefix stays in "evaluate"; None keeps
    this importable from tests that predate the instrumentation.
    ``batch_code_pool_lock`` (#1149 P2-2): None (single claim) — this
    candidate probes the node-limit row and acquires the shared code-pool
    lock itself; True/False (batch write phase) — the batch's
    start-of-transaction probe already decided, per-candidate acquisition is
    suppressed to keep the global lock order.
    """
    # Admission (claim_admission, #555): pause / contract / ACL / pools /
    # labels — lock-free, so the batch read phase runs the same gate. The
    # batch write phase re-runs it here as revalidation against write-time
    # state; a candidate that went stale since selection simply skips.
    manifest = admit_candidate(broker, selected, view, state)
    if manifest is None:
        return None
    kind = str(selected["kind"])
    state.attempts += 1
    # Fixed lock order across all capacity domains (issue #351), extended with
    # the EXEC-GENERATION-001 job-mutation domain (#759 phase 1c) and the
    # #1149 code-pool entry: agent-ws (agent kind only — that domain is
    # agent-only, so taking it for code would be pure queueing overhead) →
    # agent-worker → code-pool (code kind with a configured node limit only)
    # → job-mutation:<job> → request row. The code-pool lock is the one the
    # local code pool takes first in claim_lease (_lease_claims.py). The
    # batch write phase acquires it once at transaction start
    # (claim_batch_tx, #1149 P2-2) — before the per-candidate agent-ws /
    # agent-worker rungs — which stays acyclic: agent-worker:<id> is a
    # per-worker domain only same-worker claims enter, and those serialize
    # on the agent_workers row lock (prepare_claim_view) before either path,
    # so no cross-session pair ever contends on agent-worker while one side
    # holds code-pool. The request-row FOR UPDATE moved AFTER the job-mutation
    # advisory lock: the mutation side (lease_guarded_mutation) holds
    # job-mutation while cancelling queued request rows (_cancel_queued_sql),
    # so the pre-#759 order (request row → …) would AB-BA against
    # job-mutation → request row. The v82 counter folder uses non-blocking
    # try-locks and adds no ordering edge.
    if kind != "code":
        ws_domain = f"agent-ws:{selected['workspace_id']}"
        conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (ws_domain,))
    conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (f"agent-worker:{worker_id}",))
    # #1149：远程 code claim 与本地池共享 code-pool 锁（节点带 limit 行时）。
    # 单条路径（batch_code_pool_lock=None）在此 probe+acquire——先于本候选的
    # job-mutation 锁，序正确；批路径的锁决策由事务首句的集中 probe 冻结
    # （P2-2），此处不获取——前序候选的 job-mutation 已持有，中途获取会倒置
    # 全局锁序。probe/检查窗口的收口语义见 claim_node_limit 模块 docstring。
    pool_held = claim_node_limit.enter_code_pool_domain(conn, selected, kind, batch_code_pool_lock)
    conn.execute(
        "select pg_advisory_xact_lock(hashtext(%s))",
        (f"job-mutation:{selected['job_id']}",),
    )
    # Lock just this row; a competitor holding it (or a state change since
    # the unlocked read) skips to the next candidate.
    locked = conn.execute(
        "select execution_id, execution_generation from agent_execution_requests"
        " where execution_id=%s and state='queued' for update skip locked",
        (selected["execution_id"],),
    ).fetchone()
    if locked is None:
        state.skip_reasons["lock_raced"] += 1
        return None
    # Re-check job control state (under the job-mutation lock no mutation can
    # interleave): paused jobs keep the request queued for resume; terminal
    # jobs get their request cancelled so no zombie claims resurrect them.
    job = conn.execute(
        "select status, execution_paused, execution_generation from jobs where id=%s",
        (selected["job_id"],),
    ).fetchone()
    if job is None:
        # 防御分支：请求行 job_id 外键 on delete cascade 且行已被本事务锁住，
        # 现行 schema 下不可达；保留以防 schema 漂移时 job["…"] 取值崩溃（#955）。
        cancel_request(conn, selected["execution_id"])
        state.skip_reasons["job_missing"] += 1
        return None
    if job["execution_paused"] or job["status"] == "paused":
        state.skip_reasons["job_paused"] += 1
        return None
    if job["status"] not in RUNNABLE_JOB_STATUSES:
        cancel_request(conn, selected["execution_id"])
        state.skip_reasons["job_terminal"] += 1
        return None
    # EXEC-GENERATION-001 CAS: the request carries the epoch the dispatch
    # evaluated; a rerun/run-to/upgrade bump since then makes this claim
    # stale. Cancel with the same semantics as the mutation side's
    # _cancel_queued_sql (terminal state + manifest trim); the node stays
    # pending and the next poll pass re-enqueues against the fresh epoch.
    request_generation = int(locked["execution_generation"])
    if int(job["execution_generation"]) != request_generation:
        cancel_request(conn, selected["execution_id"])
        state.skip_reasons["generation_stale"] += 1
        return None

    # Workspace-level capacity is agent-only (batch 2 decision 2); the ws
    # lock and the cap check are both agent-branch-only.
    if kind != "code":
        capacity = conn.execute(
            "select max_concurrency from workspace_agent_capacities where workspace_id=%s",
            (selected["workspace_id"],),
        ).fetchone()
        if capacity is not None:
            ws_active = conn.execute(
                "select count(*) as cnt from agent_execution_requests"
                " where workspace_id=%s and state='claimed' and kind='agent'",
                (selected["workspace_id"],),
            ).fetchone() or {"cnt": 0}
            if int(ws_active["cnt"]) >= int(capacity["max_concurrency"]):
                # Lost the race for this workspace's last slot; try the next.
                state.skip_reasons["capacity_raced"] += 1
                return None

    # Node-level concurrency limit for remote code claims (issue #1149): the
    # limit previously gated only the local code pool, so Worker-claimed code
    # executions bypassed it entirely. The gate (claim_node_limit) reads the
    # current limit value here — claim time, runtime-mutable setting, queued
    # requests are never fail-fasted on a setting change — and counts the
    # node's active executor_leases without filtering executor_id (local and
    # remote claims merge on the same table; shard candidates included).
    # Enforcement only under the code-pool lock: a row that appeared after
    # the probe (first-config race, P2-1) skips with node_limit_appeared —
    # never count unlocked. Over-limit keeps the request queued with the
    # capacity_full semantics; the unclaimable sweeper never reaps it.
    if not claim_node_limit.code_claim_admits(conn, selected, kind, state, pool_held):
        return None

    # Writes stage boundary (#448, #461 review): everything above is evaluate
    # (locks + admission checks); from here on the claim only writes. Close
    # evaluate here, not inside claim_windows's loop close. Known noise
    # (#461): the two post-boundary skips below (shard_not_pending /
    # node_not_pending) run their cancel_request write on the evaluate side
    # of the NEXT candidate's boundary — one rare terminal-write leak per
    # raced node, accepted rather than pre-boundary-guessing races.
    if timer is not None:
        timer.stage("evaluate")

    # Shard-aware claiming (#389): a kind='code' manifest may carry a shard
    # identity (shard_index top-level key). try_start_shard binds this
    # execution_id to its node_shards row (the row-level dedup) and performs
    # the same job_nodes → running flip; the plain flip would orphan the
    # shard row (finish_shard_execution would never find it).
    shard_index = manifest.get("shard_index")
    if shard_index is not None:
        if not try_start_shard(
            conn,
            selected["job_id"],
            selected["node_key"],
            int(shard_index),
            selected["execution_id"],
            datetime.now(UTC),
            execution_generation=request_generation,
        ):
            cancel_request(conn, selected["execution_id"])
            state.skip_reasons["shard_not_pending"] += 1
            return None
    else:
        # EXEC-GENERATION-001：与 code 池 claim_lease 同纪律——翻 running 盖
        # 当前代次戳（CAS 已验证 == jobs 现值），否则旁支旧戳行在 claim 后
        # 仍带旧戳，成孤儿时 recover 的代次闸门会拒绝复位。
        updated = conn.execute(
            "update job_nodes set status='running', stale_reason='', error_message='',"
            " started_at=current_timestamp, finished_at=null, execution_generation=%s"
            " where job_id=%s and node_key=%s and status in ('pending', 'ready', 'stale')",
            (request_generation, selected["job_id"], selected["node_key"]),
        )
        if updated.rowcount == 0:
            cancel_request(conn, selected["execution_id"])
            state.skip_reasons["node_not_pending"] += 1
            return None

    # Promote 写入段（node_runs/lease/request/jobs + #551 queue_wait 折叠）
    # 在 claim_promote.py——预算拆分，evaluate 只留准入与竞态语义。
    lease_id, node_run_id = promote_claim(
        broker,
        conn,
        worker_id,
        selected,
        manifest,
        kind,
        execution_generation=request_generation,
    )
    return AgentClaim(
        execution_id=selected["execution_id"],
        workspace_id=selected["workspace_id"],
        job_id=selected["job_id"],
        node_key=selected["node_key"],
        agent_id=selected["agent_id"],
        lease_id=lease_id,
        node_run_id=node_run_id,
        manifest=manifest,
        kind=kind,
        # #490: claim.granted reads the resolved runtime off the claim.
        runtime=str(selected["runtime"]),
        execution_generation=request_generation,
    )
