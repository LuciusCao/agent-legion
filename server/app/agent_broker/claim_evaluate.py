"""Per-candidate claim evaluation for the Agent execution queue.

Split out of ``claim_scan.py`` for the file-size budget: given one candidate
row (from the bounded window scan) and the Worker view, try to claim it —
compatibility filters, row lock, job re-check, capacity enforcement, and the
lease + run inserts. Must run inside the caller's transaction.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from server.app.agent_broker import agent_claim_compatibility
from server.app.agent_broker.claim_paths import claim_log_path
from server.app.agent_broker.claim_scan import (
    RUNNABLE_JOB_STATUSES,
    AgentClaim,
    ClaimRacedError,
    ScanState,
    WorkerView,
    labels_satisfy,
)
from server.app.agent_broker.code_manifest import CODE_MANIFEST_TRIM
from server.app.agent_workers import CODE_PROTOCOL_VERSION

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker


def evaluate_candidate(
    broker: AgentExecutionBroker,
    conn: Any,
    worker_id: str,
    selected: Mapping[str, Any],
    view: WorkerView,
    state: ScanState,
) -> AgentClaim | None:
    """Try to claim one candidate; record the skip reason when it loses.

    Returns the claim on success. On a skip, ``state.skip_reasons`` gains
    exactly one entry naming the cause; skips past the compatibility filters
    (row lock and beyond) also consume one bounded claim attempt.
    """
    selected_workspace = str(selected["workspace_id"])
    if selected_workspace not in state.pause_cache:
        check = broker.is_workspace_paused
        state.pause_cache[selected_workspace] = (
            bool(check(selected_workspace)) if check is not None else False
        )
    if state.pause_cache[selected_workspace]:
        # Paused workspace: keep the request queued for resume.
        state.skip_reasons["workspace_paused"] += 1
        return None
    kind = str(selected["kind"])
    # Code manifests are fully frozen at enqueue: no revision-time execution
    # re-resolution (the payload carries no provider/model).
    manifest = (
        json.loads(str(selected["manifest_json"]))
        if kind == "code"
        else agent_claim_compatibility.live_claim_manifest(selected)
    )
    # Workspace admission scope from the server-side registration snapshot
    # (EXEC-WORKERACL-001): [] means all workspaces; a non-empty list
    # restricts this Worker to those workspaces. Never trust Worker-
    # supplied fields for this.
    if view.allowed_workspaces and selected_workspace not in view.allowed_workspaces:
        state.skip_reasons["workspace_not_allowed"] += 1
        return None
    # Dual capacity pools: a candidate whose pool is exhausted is skipped,
    # not fatal — the other pool may still have claimable candidates.
    if kind == "code":
        # Defense in depth behind the register-time rejection: a v1 row that
        # predates it must never hold code executions (v1 heartbeats carry no
        # cancel body, and old binaries cannot unpack code bundles).
        if view.protocol_version < CODE_PROTOCOL_VERSION:
            state.skip_reasons["protocol_version_too_old"] += 1
            return None
        if view.code_active >= view.code_capacity:
            state.skip_reasons["code_capacity_full"] += 1
            return None
        # Code Workers carry no runtime/model declarations: the code text
        # rides the bundle, so capability matching is the whole contract.
        capability = str(selected["capability"])
        if capability not in view.capabilities and "*" not in view.capabilities:
            state.skip_reasons["capability_or_model_mismatch"] += 1
            return None
    else:
        if view.agent_active >= view.agent_capacity:
            state.skip_reasons["capacity_full"] += 1
            return None
        if selected["runtime"] not in view.runtimes:
            state.skip_reasons["runtime_mismatch"] += 1
            return None
        if not agent_claim_compatibility.worker_can_run(
            selected, manifest, view.capabilities, view.models
        ):
            state.skip_reasons["capability_or_model_mismatch"] += 1
            return None
    if not labels_satisfy(
        view.labels, json.loads(selected["definition_json"]).get("requires_labels", {})
    ):
        state.skip_reasons["labels_mismatch"] += 1
        return None
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
    # Fixed lock order across all capacity domains: the workspace-level
    # Agent capacity domain first, then the Worker machine domain.
    ws_domain = f"agent-ws:{selected['workspace_id']}"
    conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (ws_domain,))
    conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (f"agent-worker:{worker_id}",))

    # Workspace-level capacity is agent-only; code has no workspace cap in
    # this phase (batch 2 decision 2).
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

    log_path = claim_log_path(manifest, broker.data_dir)
    # Dispatch-time config audit (CONFIG-RUNTIME-MUTABLE-001): the manifest
    # config is the non-secret resolved config built at enqueue on the Host —
    # frozen keys repeat the intake snapshot, runtime_mutable keys carry the
    # enqueue-time re-resolution. Secret values never enter the manifest
    # (CONFIG-MANIFEST-001), so this is safe to persist.
    config_snapshot_json = json.dumps(manifest.get("config") or {}, sort_keys=True, default=str)
    run = conn.execute(
        """
        insert into node_runs(
          job_id, node_key, status, command_json, log_path, run_dir, session_dir,
          started_at, config_snapshot_json
        ) values (%s, %s, 'running', '[]', %s, '', '', current_timestamp, %s)
        returning id
        """,
        (selected["job_id"], selected["node_key"], log_path, config_snapshot_json),
    ).fetchone()
    if run is None:
        raise RuntimeError("node run insert did not return an id")
    lease_id = str(uuid.uuid4())
    expires_at = datetime.now(UTC) + timedelta(seconds=broker.lease_ttl_seconds)
    # Code leases share the 'agent:' prefix so the generic lease sweeper
    # keeps leaving them to the Agent broker sweep (requeue semantics).
    executor_id = (
        f"agent:code:{selected['agent_id']}" if kind == "code" else f"agent:{selected['agent_id']}"
    )
    conn.execute(
        """
        insert into executor_leases(
          id, execution_id, executor_id, workspace_id, job_id, workflow_key,
          node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at
        ) values (%s, %s, %s, %s, %s, %s, %s, %s, 'active', current_timestamp, current_timestamp, %s)
        """,
        (
            lease_id,
            selected["execution_id"],
            executor_id,
            selected["workspace_id"],
            selected["job_id"],
            selected["workflow_key"],
            selected["node_key"],
            run["id"],
            expires_at,
        ),
    )
    conn.execute(
        """
        update agent_execution_requests set
          state='claimed', worker_id=%s, lease_id=%s, node_run_id=%s,
          attempt=attempt+1, claimed_at=current_timestamp, heartbeat_at=current_timestamp
        where execution_id=%s and state='queued'
        """,
        (worker_id, lease_id, run["id"], selected["execution_id"]),
    )
    promoted = conn.execute(
        "update jobs set status='running', updated_at=current_timestamp"
        " where id=%s and status in ('queued', 'running') and execution_paused=0",
        (selected["job_id"],),
    )
    if promoted.rowcount == 0:
        # Pause/failure landed mid-claim; roll the whole claim back so the
        # request stays queued instead of resurrecting the job.
        raise ClaimRacedError()
    return AgentClaim(
        execution_id=selected["execution_id"],
        workspace_id=selected["workspace_id"],
        job_id=selected["job_id"],
        workflow_key=selected["workflow_key"],
        node_key=selected["node_key"],
        agent_id=selected["agent_id"],
        lease_id=lease_id,
        node_run_id=int(run["id"]),
        manifest=manifest,
        kind=kind,
    )


def cancel_request(conn: Any, execution_id: str) -> None:
    conn.execute(
        "update agent_execution_requests set state='cancelled',"
        " finished_at=current_timestamp, manifest_json="
        + CODE_MANIFEST_TRIM
        + " where execution_id=%s",
        (execution_id,),
    )
