"""Per-candidate claim evaluation for the Agent execution queue.

Split out of ``claim_scan.py`` for the file-size budget: given one candidate
row (from the bounded window scan, or from the #555 batch read phase) and
the Worker view, try to claim it — admission, row lock, job re-check,
capacity enforcement. The lock-free admission filters live in
``claim_admission.py`` (#555 — shared with the batch read phase, which runs
them outside any lock window); the promote write sequence (run row / lease /
request flip / jobs promote / queue-wait gauge) lives in
``claim_promote.py`` (#551). Must run inside the caller's transaction.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

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
) -> AgentClaim | None:
    """Try to claim one candidate; on a skip, record the cause in state.skip_reasons.

    ``timer`` (#448) closes the promote-write sequence below into the
    "writes" stage — the lock/validate prefix stays in "evaluate"; None keeps
    this importable from tests that predate the instrumentation.
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
    # Lock just this row; a competitor holding it (or a state change since
    # the unlocked read) skips to the next candidate.
    locked = conn.execute(
        "select execution_id from agent_execution_requests"
        " where execution_id=%s and state='queued' for update skip locked",
        (selected["execution_id"],),
    ).fetchone()
    if locked is None:
        state.skip_reasons["lock_raced"] += 1
        return None
    # Re-check job control state: paused jobs keep the request queued for
    # resume; terminal jobs get their request cancelled so no zombie claims
    # resurrect them.
    job = conn.execute(
        "select status, execution_paused from jobs where id=%s",
        (selected["job_id"],),
    ).fetchone()
    if job is None:
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
    # Fixed lock order across all capacity domains (issue #351): workspace
    # Agent domain first, then the Worker machine domain. A code claim skips
    # the workspace lock entirely — that domain is agent-only, so taking it
    # for code would be pure queueing overhead. The order stays acyclic:
    # agent takes ws→worker, code takes only worker.
    if kind != "code":
        ws_domain = f"agent-ws:{selected['workspace_id']}"
        conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (ws_domain,))
    conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (f"agent-worker:{worker_id}",))

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
        ):
            cancel_request(conn, selected["execution_id"])
            state.skip_reasons["shard_not_pending"] += 1
            return None
    else:
        updated = conn.execute(
            "update job_nodes set status='running', stale_reason='', error_message='',"
            " started_at=current_timestamp, finished_at=null"
            " where job_id=%s and node_key=%s and status in ('pending', 'ready', 'stale')",
            (selected["job_id"], selected["node_key"]),
        )
        if updated.rowcount == 0:
            cancel_request(conn, selected["execution_id"])
            state.skip_reasons["node_not_pending"] += 1
            return None

    # Promote 写入段（node_runs/lease/request/jobs + #551 queue_wait 折叠）
    # 在 claim_promote.py——预算拆分，evaluate 只留准入与竞态语义。
    lease_id, node_run_id = promote_claim(broker, conn, worker_id, selected, manifest, kind)
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
    )
