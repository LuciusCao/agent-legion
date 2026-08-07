"""Candidate window scan for the Agent claim transaction.

Split out of ``claim.py`` for the file-size budget: the bounded candidate
query, the per-candidate evaluation (compatibility filters, row lock, job
re-check, capacity enforcement, lease + run inserts) and the skip-reason
accounting live here; ``claim.py`` keeps the Worker-level setup and the
scan-round loop. Functions take the broker instance as their first argument
and must run inside the caller's transaction.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from server.app.agent_broker import agent_claim_compatibility

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker

# Bounded claim scan rounds: (per-workspace head limit, global window). The
# first round matches the historical fixed window, so healthy queues see no
# behaviour change; deeper rounds only engage when the window came back
# saturated yet nothing in it was claimable, so a queue head poisoned by
# unclaimable requests can no longer deadlock a workspace (head-of-line
# blocking, issue #13).
SCAN_ROUNDS: tuple[tuple[int, int], ...] = ((8, 256), (64, 2048), (512, 16384))
MAX_CLAIM_ATTEMPTS = 32
RUNNABLE_JOB_STATUSES = ("queued", "running")


class ClaimRacedError(Exception):
    """The job left the runnable set mid-claim; roll the whole claim back."""


@dataclass(frozen=True)
class AgentClaim:
    execution_id: str
    workspace_id: str
    job_id: str
    workflow_key: str
    node_key: str
    agent_id: str
    lease_id: str
    node_run_id: int
    manifest: dict[str, Any]


@dataclass(frozen=True)
class WorkerView:
    """Server-side Worker declarations relevant to candidate matching."""

    runtimes: set[str]
    capabilities: set[str]
    models: set[tuple[str, str]]
    labels: dict[str, Any]
    allowed_workspaces: set[str]


@dataclass
class ScanState:
    """Mutable per-claim-pass accounting shared across all scan rounds."""

    attempts: int = 0
    skip_reasons: Counter[str] = field(default_factory=Counter)
    pause_cache: dict[str, bool] = field(default_factory=dict)


def fetch_candidates(conn: Any, per_workspace: int, window: int) -> list[Any]:
    # Candidates are read WITHOUT row locks (a bounded per-workspace window
    # keeps small workspaces visible behind a deep queue); only the single
    # row actually being claimed is locked below, by PK. Capacity is
    # workspace-level (no workspace_agent_capacities row = unlimited).
    # Eligibility is an EXISTS probe per workspaces row — a `distinct
    # workspace_id` scan would walk the entire queued index on every claim.
    rows: list[Any] = conn.execute(
        """
        with eligible_workspaces as (
          select ws.id as workspace_id
          from workspaces ws
          left join workspace_agent_capacities w on w.workspace_id=ws.id
          where exists (select 1 from agent_execution_requests q
                        where q.workspace_id=ws.id and q.state='queued')
            and (select count(*) from agent_execution_requests active
                 where active.workspace_id=ws.id and active.state='claimed'
                ) < coalesce(w.max_concurrency, 2147483647)
        )
        select r.*, wr.definition_json as revision_definition_json
        from eligible_workspaces ws
        cross join lateral (
          select r2.*,
                 d.definition_json::jsonb->>'runtime' as runtime,
                 d.definition_json::jsonb->>'capability' as capability,
                 d.definition_json
          from agent_execution_requests r2
          join versioned_entities d
            on d.entity_type='agent' and d.workspace_id is null
           and d.entity_key=r2.agent_id and d.definition_hash=r2.agent_definition_hash
           -- Quality replay pins match their immutable version row (any
           -- status); unpinned requests match the currently published row.
           and ((r2.pinned_agent_version is not null
                 and d.version=r2.pinned_agent_version)
                or (r2.pinned_agent_version is null and d.status='published'))
          where r2.workspace_id=ws.workspace_id and r2.state='queued'
          order by r2.queued_at, r2.execution_id limit %s
        ) r
        join jobs j on j.id=r.job_id
        left join workflow_revisions wr on wr.id=j.workflow_revision_id
        order by r.queued_at, r.execution_id limit %s
        """,
        (per_workspace, window),
    ).fetchall()
    return rows


def window_saturated(candidates: list[Any], per_workspace: int, window: int) -> bool:
    """True when a deeper window could still surface fresh candidates.

    Saturated means the global window filled up or some workspace returned a
    full per-workspace page — either way unclaimable entries may be hiding
    claimable ones behind them, so the next scan round is worth running."""
    if len(candidates) >= window:
        return True
    counts = Counter(str(row["workspace_id"]) for row in candidates)
    return any(count >= per_workspace for count in counts.values())


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
    manifest = agent_claim_compatibility.live_claim_manifest(selected)
    # Workspace admission scope from the server-side registration snapshot
    # (EXEC-WORKERACL-001): [] means all workspaces; a non-empty list
    # restricts this Worker to those workspaces. Never trust Worker-
    # supplied fields for this.
    if view.allowed_workspaces and selected_workspace not in view.allowed_workspaces:
        state.skip_reasons["workspace_not_allowed"] += 1
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

    capacity = conn.execute(
        "select max_concurrency from workspace_agent_capacities where workspace_id=%s",
        (selected["workspace_id"],),
    ).fetchone()
    if capacity is not None:
        ws_active = conn.execute(
            "select count(*) as cnt from agent_execution_requests"
            " where workspace_id=%s and state='claimed'",
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

    log_path = str(manifest.get("log_path", ""))
    run = conn.execute(
        """
        insert into node_runs(
          job_id, node_key, status, command_json, log_path, run_dir, session_dir, started_at
        ) values (%s, %s, 'running', '[]', %s, '', '', current_timestamp)
        returning id
        """,
        (selected["job_id"], selected["node_key"], log_path),
    ).fetchone()
    if run is None:
        raise RuntimeError("node run insert did not return an id")
    lease_id = str(uuid.uuid4())
    expires_at = datetime.now(UTC) + timedelta(seconds=broker.lease_ttl_seconds)
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
            f"agent:{selected['agent_id']}",
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
    )


def cancel_request(conn: Any, execution_id: str) -> None:
    conn.execute(
        "update agent_execution_requests set state='cancelled',"
        " finished_at=current_timestamp where execution_id=%s",
        (execution_id,),
    )


def fair_candidate_order(rows: list[dict[str, Any]], cursor: int) -> Iterator[dict[str, Any]]:
    """Interleave candidates across workspaces, starting rotation at ``cursor``.

    Per-workspace order stays queued_at-FIFO; only the cross-workspace order
    rotates so a deep queue in one workspace cannot starve the others."""
    by_workspace: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_workspace.setdefault(str(row["workspace_id"]), []).append(row)
    keys = list(by_workspace)
    if not keys:
        return
    start = cursor % len(keys)
    rotated = keys[start:] + keys[:start]
    depth = 0
    while True:
        yielded = False
        for key in rotated:
            group = by_workspace[key]
            if depth < len(group):
                yield group[depth]
                yielded = True
        if not yielded:
            return
        depth += 1


def labels_satisfy(actual: Mapping[str, Any], required: Mapping[str, Any]) -> bool:
    return all(str(actual.get(key)) == str(value) for key, value in required.items())
