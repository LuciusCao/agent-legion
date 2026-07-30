"""Atomic claim transaction for the Agent execution queue.

Split out of ``agent_broker.py`` so the broker module only carries the queue
protocol; mirrors the ``executors/_lease_*.py`` layout. Functions take the
broker instance as their first argument and must run inside the caller's
transaction unless noted otherwise.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from server.app import agent_claim_compatibility
from server.app.agent_worker_capacity import sync_declared_capacity, touch_worker

if TYPE_CHECKING:
    from server.app.agent_broker import AgentExecutionBroker

_CANDIDATE_WINDOW = 256
_CANDIDATES_PER_WORKSPACE = 8
_MAX_CLAIM_ATTEMPTS = 32
_RUNNABLE_JOB_STATUSES = ("queued", "running")


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


def claim_in_transaction(
    broker: AgentExecutionBroker,
    conn: Any,
    worker_id: str,
    declared_max_concurrency: int | None = None,
) -> AgentClaim | None:
    worker = conn.execute(
        "select * from agent_workers where worker_id=? for update", (worker_id,)
    ).fetchone()
    if worker is None or worker["revoked_at"] is not None:
        raise ValueError("unknown or revoked Agent Worker")
    max_concurrency = sync_declared_capacity(conn, worker, declared_max_concurrency)
    runtimes = set(json.loads(worker["runtimes_json"]))
    capabilities, models = agent_claim_compatibility.worker_declarations(worker)
    labels = json.loads(worker["labels_json"])
    # Workspace admission scope from the server-side registration snapshot
    # (EXEC-WORKERACL-001): [] means all workspaces; a non-empty list
    # restricts this Worker to those workspaces. Never trust Worker-
    # supplied fields for this.
    allowed_workspaces = set(json.loads(worker["allowed_workspaces_json"] or "[]"))
    worker_active = conn.execute(
        "select count(*) as cnt from agent_execution_requests"
        " where worker_id=? and state='claimed'",
        (worker_id,),
    ).fetchone() or {"cnt": 0}
    if int(worker_active["cnt"]) >= max_concurrency:
        touch_worker(conn, worker_id)
        return None
    # Candidates are read WITHOUT row locks (a bounded per-workspace window
    # keeps small workspaces visible behind a deep queue); only the single
    # row actually being claimed is locked below, by PK. Capacity is
    # workspace-level: a workspace without a workspace_agent_capacities row
    # has no configured limit and is treated as unlimited. Eligibility is
    # decided once per workspace and the lateral branch reads only each
    # workspace's queued head (idx_agent_requests_queued_head) — the old
    # per-row count subquery made every claim O(queue depth).
    candidates = conn.execute(
        """
        with eligible_workspaces as (
          select q.workspace_id
          from (select distinct workspace_id from agent_execution_requests where state='queued') q
          left join workspace_agent_capacities w on w.workspace_id=q.workspace_id
          where (select count(*) from agent_execution_requests active
                 where active.workspace_id=q.workspace_id and active.state='claimed'
                ) < coalesce(w.max_concurrency, 2147483647)
        )
        select r.*, wr.definition_json as revision_definition_json
        from eligible_workspaces ws
        cross join lateral (
          select r2.*, d.runtime, d.capability, d.definition_json
          from agent_execution_requests r2
          join agent_definitions d
            on d.agent_id=r2.agent_id and d.definition_hash=r2.agent_definition_hash and d.enabled=1
          where r2.workspace_id=ws.workspace_id and r2.state='queued'
          order by r2.queued_at, r2.execution_id limit ?
        ) r
        join jobs j on j.id=r.job_id
        left join workflow_revisions wr on wr.id=j.workflow_revision_id
        order by r.queued_at, r.execution_id limit ?
        """,
        (_CANDIDATES_PER_WORKSPACE, _CANDIDATE_WINDOW),
    ).fetchall()
    cursor = next(broker._fairness_counter)
    attempts = 0
    pause_cache: dict[str, bool] = {}
    for selected in _fair_candidate_order(candidates, cursor):
        if attempts >= _MAX_CLAIM_ATTEMPTS:
            break
        selected_workspace = str(selected["workspace_id"])
        if selected_workspace not in pause_cache:
            check = broker.is_workspace_paused
            pause_cache[selected_workspace] = (
                bool(check(selected_workspace)) if check is not None else False
            )
        if pause_cache[selected_workspace]:
            # Paused workspace: keep the request queued for resume.
            continue
        manifest = agent_claim_compatibility.live_claim_manifest(selected)
        if (
            (allowed_workspaces and selected_workspace not in allowed_workspaces)
            or selected["runtime"] not in runtimes
            or not agent_claim_compatibility.worker_can_run(
                selected, manifest, capabilities, models
            )
            or not _labels_satisfy(
                labels, json.loads(selected["definition_json"]).get("requires_labels", {})
            )
        ):
            continue
        attempts += 1
        # Lock just this row; a competitor holding it (or a state change
        # since the unlocked read) skips to the next candidate.
        locked = conn.execute(
            "select execution_id from agent_execution_requests"
            " where execution_id=? and state='queued' for update skip locked",
            (selected["execution_id"],),
        ).fetchone()
        if locked is None:
            continue
        # Re-check job control state: paused jobs keep the request queued
        # for resume; terminal jobs get their request cancelled so no
        # zombie claims resurrect them.
        job = conn.execute(
            "select status, execution_paused from jobs where id=?",
            (selected["job_id"],),
        ).fetchone()
        if job is None:
            cancel_request(conn, selected["execution_id"])
            continue
        if job["execution_paused"] or job["status"] == "paused":
            continue
        if job["status"] not in _RUNNABLE_JOB_STATUSES:
            cancel_request(conn, selected["execution_id"])
            continue
        # Fixed lock order across all capacity domains: the workspace-level
        # Agent capacity domain first, then the Worker machine domain.
        ws_domain = f"agent-ws:{selected['workspace_id']}"
        conn.execute("select pg_advisory_xact_lock(hashtext(?))", (ws_domain,))
        conn.execute("select pg_advisory_xact_lock(hashtext(?))", (f"agent-worker:{worker_id}",))

        capacity = conn.execute(
            "select max_concurrency from workspace_agent_capacities where workspace_id=?",
            (selected["workspace_id"],),
        ).fetchone()
        if capacity is not None:
            ws_active = conn.execute(
                "select count(*) as cnt from agent_execution_requests"
                " where workspace_id=? and state='claimed'",
                (selected["workspace_id"],),
            ).fetchone() or {"cnt": 0}
            if int(ws_active["cnt"]) >= int(capacity["max_concurrency"]):
                # Lost the race for this workspace's last slot; try the next.
                continue

        updated = conn.execute(
            "update job_nodes set status='running', stale_reason='', error_message='',"
            " started_at=current_timestamp, finished_at=null"
            " where job_id=? and node_key=? and status in ('pending', 'ready', 'stale')",
            (selected["job_id"], selected["node_key"]),
        )
        if updated.rowcount == 0:
            cancel_request(conn, selected["execution_id"])
            continue

        log_path = str(manifest.get("log_path", ""))
        run = conn.execute(
            """
            insert into node_runs(
              job_id, node_key, status, command_json, log_path, run_dir, session_dir, started_at
            ) values (?, ?, 'running', '[]', ?, '', '', current_timestamp)
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
            ) values (?, ?, ?, ?, ?, ?, ?, ?, 'active', current_timestamp, current_timestamp, ?)
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
              state='claimed', worker_id=?, lease_id=?, node_run_id=?,
              attempt=attempt+1, claimed_at=current_timestamp, heartbeat_at=current_timestamp
            where execution_id=? and state='queued'
            """,
            (worker_id, lease_id, run["id"], selected["execution_id"]),
        )
        promoted = conn.execute(
            "update jobs set status='running', updated_at=current_timestamp"
            " where id=? and status in ('queued', 'running') and execution_paused=0",
            (selected["job_id"],),
        )
        if promoted.rowcount == 0:
            # Pause/failure landed mid-claim; roll the whole claim back so
            # the request stays queued instead of resurrecting the job.
            raise ClaimRacedError()
        touch_worker(conn, worker_id)
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
    touch_worker(conn, worker_id)
    return None


def cancel_request(conn: Any, execution_id: str) -> None:
    conn.execute(
        "update agent_execution_requests set state='cancelled',"
        " finished_at=current_timestamp where execution_id=?",
        (execution_id,),
    )


def _fair_candidate_order(rows: list[dict[str, Any]], cursor: int) -> Iterator[dict[str, Any]]:
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


def _labels_satisfy(actual: Mapping[str, Any], required: Mapping[str, Any]) -> bool:
    return all(str(actual.get(key)) == str(value) for key, value in required.items())
