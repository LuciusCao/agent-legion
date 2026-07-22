from __future__ import annotations

import itertools
import json
import re
import time
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from psycopg import IntegrityError

from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction

if TYPE_CHECKING:
    from server.app.agents import AgentStatusManager

_CANDIDATE_WINDOW = 256
_CANDIDATES_PER_WORKSPACE = 8
_MAX_CLAIM_ATTEMPTS = 32
_RUNNABLE_JOB_STATUSES = ("queued", "running")
_ACTIVE_LEASE_CONSTRAINT = "idx_agent_requests_one_active_node"
_SAFE_BUNDLE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


class _ClaimRacedError(Exception):
    """The job left the runnable set mid-claim; roll the whole claim back."""


@dataclass(frozen=True)
class AgentExecutionRequest:
    workspace_id: str
    job_id: str
    workflow_key: str
    node_key: str
    agent_id: str
    agent_definition_hash: str
    # Legacy node-level limit declared by the workflow (0 = unset). Enforcement
    # is workspace-level; the broker only stores an audit snapshot.
    node_concurrency_limit: int
    manifest: Mapping[str, Any]
    execution_id: str = ""


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


class AgentExecutionBroker:
    """PostgreSQL Agent queue with atomic node and Worker capacity enforcement."""

    def __init__(
        self,
        database_dsn: DatabaseDsn,
        *,
        lease_ttl_seconds: int = 90,
        bundle_dir: Path | None = None,
        requeue_limit: int = 3,
        agent_status: AgentStatusManager | None = None,
    ) -> None:
        self.database_dsn = database_dsn
        self.lease_ttl_seconds = lease_ttl_seconds
        self.bundle_dir = bundle_dir
        self.requeue_limit = requeue_limit
        # Optional status-panel sink: when wired, claim/finish transitions are
        # mirrored into the /api/agents feed so the workspace panel shows
        # distributed executions, not only in-process runners.
        self.agent_status = agent_status
        # Rotating cursor for bounded cross-workspace fairness (EXEC-FAIRNESS
        # style): each claim pass starts candidate evaluation at the next
        # workspace instead of always at the globally oldest request.
        self._fairness_counter = itertools.count()

    def has_active_request(self, job_id: str, node_key: str) -> bool:
        with read_connection(self.database_dsn) as conn:
            row = conn.execute(
                "select 1 from agent_execution_requests"
                " where job_id=? and node_key=? and state in ('queued', 'claimed') limit 1",
                (job_id, node_key),
            ).fetchone()
        return row is not None

    def enqueue(self, request: AgentExecutionRequest) -> str | None:
        if request.node_concurrency_limit < 0:
            raise ValueError("node_concurrency_limit must not be negative")
        execution_id = request.execution_id or str(uuid.uuid4())
        try:
            with write_transaction(self.database_dsn) as conn:
                route = conn.execute(
                    """
                    select target_kind, target_id from workspace_node_routes
                    where workspace_id=? and workflow_key=? and node_key=?
                    """,
                    (request.workspace_id, request.workflow_key, request.node_key),
                ).fetchone()
                if route is None or route["target_kind"] != "agent":
                    raise ValueError("workspace node is not routed to an Agent")
                if route["target_id"] != request.agent_id:
                    raise ValueError("workspace node Agent route changed before enqueue")
                definition = conn.execute(
                    "select definition_hash from agent_definitions where agent_id=? and enabled=1",
                    (request.agent_id,),
                ).fetchone()
                if (
                    definition is None
                    or definition["definition_hash"] != request.agent_definition_hash
                ):
                    raise ValueError("Agent definition is unavailable or changed before enqueue")
                capacity = conn.execute(
                    "select max_concurrency from workspace_agent_capacities where workspace_id=?",
                    (request.workspace_id,),
                ).fetchone()
                # Audit-only snapshot of the governing limit at enqueue time:
                # the legacy node-level value when the workflow still declares
                # one, else the current workspace-level cap; 1 records "no
                # configured limit (unlimited)". Never used for enforcement.
                stored_limit = request.node_concurrency_limit
                if stored_limit <= 0:
                    stored_limit = int(capacity["max_concurrency"]) if capacity is not None else 1
                conn.execute(
                    """
                    insert into agent_execution_requests(
                      execution_id, workspace_id, job_id, workflow_key, node_key,
                      agent_id, agent_definition_hash, node_concurrency_limit,
                      queued_at, manifest_json
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, current_timestamp, ?)
                    """,
                    (
                        execution_id,
                        request.workspace_id,
                        request.job_id,
                        request.workflow_key,
                        request.node_key,
                        request.agent_id,
                        request.agent_definition_hash,
                        stored_limit,
                        json.dumps(dict(request.manifest), ensure_ascii=False, sort_keys=True),
                    ),
                )
        except IntegrityError as exc:
            # Only the one-active-request-per-node unique index means "already
            # enqueued". Anything else (FK violations, other constraints) is a
            # real error and must surface.
            diag = getattr(exc, "diag", None)
            constraint = getattr(diag, "constraint_name", None) if diag is not None else None
            if getattr(exc, "sqlstate", None) == "23505" and constraint == _ACTIVE_LEASE_CONSTRAINT:
                return None
            raise
        return execution_id

    def claim(self, worker_id: str) -> AgentClaim | None:
        try:
            with write_transaction(self.database_dsn) as conn:
                claimed = self._claim_in_transaction(conn, worker_id)
        except _ClaimRacedError:
            return None
        if claimed is not None:
            self._notify_agent_claimed(claimed)
        return claimed

    def _notify_agent_claimed(self, claimed: AgentClaim) -> None:
        manager = self.agent_status
        if manager is None:
            return
        max_tasks = 1
        with read_connection(self.database_dsn) as conn:
            capacity = conn.execute(
                "select max_concurrency from workspace_agent_capacities where workspace_id=?",
                (claimed.workspace_id,),
            ).fetchone()
        if capacity is not None:
            max_tasks = int(capacity["max_concurrency"])
        manager.ensure_workspace_agent(claimed.agent_id, claimed.workspace_id, max_tasks=max_tasks)
        manager.set_busy(claimed.agent_id, "", workspace_id=claimed.workspace_id)

    def _notify_agent_released(self, agent_id: str, workspace_id: str) -> None:
        if self.agent_status is not None:
            self.agent_status.set_idle(agent_id, workspace_id=workspace_id)

    def _claim_in_transaction(self, conn: Any, worker_id: str) -> AgentClaim | None:
        worker = conn.execute(
            "select * from agent_workers where worker_id=? for update", (worker_id,)
        ).fetchone()
        if worker is None or worker["revoked_at"] is not None:
            raise ValueError("unknown or revoked Agent Worker")
        runtimes = set(json.loads(worker["runtimes_json"]))
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
        if int(worker_active["cnt"]) >= int(worker["max_concurrency"]):
            self._touch_worker(conn, worker_id)
            return None
        # Candidates are read WITHOUT row locks (a bounded per-workspace window
        # keeps small workspaces visible behind a deep queue); only the single
        # row actually being claimed is locked below, by PK. Capacity is
        # workspace-level: a workspace without a workspace_agent_capacities row
        # has no configured limit and is treated as unlimited.
        candidates = conn.execute(
            """
            select * from (
              select r.*, d.runtime, d.definition_json,
                     row_number() over (
                       partition by r.workspace_id
                       order by r.queued_at, r.execution_id
                     ) as workspace_rank
              from agent_execution_requests r
              join agent_definitions d on d.agent_id=r.agent_id and d.definition_hash=r.agent_definition_hash and d.enabled=1
              left join workspace_agent_capacities w on w.workspace_id=r.workspace_id
              where r.state='queued'
                and (
                  select count(*) from agent_execution_requests active
                  where active.workspace_id=r.workspace_id and active.state='claimed'
                ) < coalesce(w.max_concurrency, 2147483647)
            ) windowed
            where workspace_rank <= ?
            order by queued_at, execution_id
            limit ?
            """,
            (_CANDIDATES_PER_WORKSPACE, _CANDIDATE_WINDOW),
        ).fetchall()
        cursor = next(self._fairness_counter)
        attempts = 0
        for selected in _fair_candidate_order(candidates, cursor):
            if attempts >= _MAX_CLAIM_ATTEMPTS:
                break
            if (
                (allowed_workspaces and str(selected["workspace_id"]) not in allowed_workspaces)
                or selected["runtime"] not in runtimes
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
                self._cancel_request(conn, selected["execution_id"])
                continue
            if job["execution_paused"] or job["status"] == "paused":
                continue
            if job["status"] not in _RUNNABLE_JOB_STATUSES:
                self._cancel_request(conn, selected["execution_id"])
                continue
            # Fixed lock order across all capacity domains: the workspace-level
            # Agent capacity domain first, then the Worker machine domain.
            ws_domain = f"agent-ws:{selected['workspace_id']}"
            conn.execute("select pg_advisory_xact_lock(hashtext(?))", (ws_domain,))
            conn.execute(
                "select pg_advisory_xact_lock(hashtext(?))", (f"agent-worker:{worker_id}",)
            )

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
                self._cancel_request(conn, selected["execution_id"])
                continue

            manifest = json.loads(selected["manifest_json"])
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
            expires_at = datetime.now(UTC) + timedelta(seconds=self.lease_ttl_seconds)
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
                raise _ClaimRacedError()
            self._touch_worker(conn, worker_id)
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
        self._touch_worker(conn, worker_id)
        return None

    @staticmethod
    def _cancel_request(conn: Any, execution_id: str) -> None:
        conn.execute(
            "update agent_execution_requests set state='cancelled',"
            " finished_at=current_timestamp where execution_id=?",
            (execution_id,),
        )

    def heartbeat(self, execution_id: str, worker_id: str, lease_id: str) -> bool:
        """Renew the lease; bound to the current lease_id so zombie attempts
        from a requeued execution cannot keep a re-claimed lease alive.

        Route note (routes owner): the heartbeat endpoint must accept the
        worker's current lease_id and pass it through."""
        expires_at = datetime.now(UTC) + timedelta(seconds=self.lease_ttl_seconds)
        with write_transaction(self.database_dsn) as conn:
            row = conn.execute(
                "select lease_id from agent_execution_requests"
                " where execution_id=? and worker_id=? and lease_id=? and state='claimed'"
                " for update",
                (execution_id, worker_id, lease_id),
            ).fetchone()
            if row is None:
                return False
            conn.execute(
                "update agent_execution_requests set heartbeat_at=current_timestamp"
                " where execution_id=?",
                (execution_id,),
            )
            conn.execute(
                "update executor_leases set heartbeat_at=current_timestamp, expires_at=?"
                " where id=? and status='active'",
                (expires_at, row["lease_id"]),
            )
            self._touch_worker(conn, worker_id)
            return True

    def claimed_payload(self, execution_id: str, worker_id: str) -> dict[str, Any] | None:
        with read_connection(self.database_dsn) as conn:
            row = conn.execute(
                "select manifest_json, lease_id, job_id, node_key from agent_execution_requests"
                " where execution_id=? and worker_id=? and state='claimed'",
                (execution_id, worker_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "manifest": json.loads(row["manifest_json"]),
            "lease_id": row["lease_id"],
            "job_id": row["job_id"],
            "node_key": row["node_key"],
        }

    def mark_done(
        self, execution_id: str, worker_id: str, lease_id: str, outcome: Mapping[str, Any]
    ) -> str | None:
        """Close the request; bound to the current lease_id so a late result
        from a previous attempt is rejected after a requeue/re-claim.

        Route note (routes owner): the result endpoint must require the
        worker's lease_id (header or metadata) and pass it through."""
        with write_transaction(self.database_dsn) as conn:
            row = conn.execute(
                "select lease_id, agent_id, workspace_id from agent_execution_requests"
                " where execution_id=? and worker_id=? and lease_id=? and state='claimed'"
                " for update",
                (execution_id, worker_id, lease_id),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "update agent_execution_requests set state='done', outcome_json=?,"
                " finished_at=current_timestamp where execution_id=?",
                (json.dumps(dict(outcome), ensure_ascii=False), execution_id),
            )
            self._touch_worker(conn, worker_id)
        self._notify_agent_released(str(row["agent_id"]), str(row["workspace_id"]))
        return str(row["lease_id"])

    def sweep_expired_claims(self) -> list[str]:
        """Requeue Worker-lost claims without leaving the workflow node running."""
        cutoff = datetime.now(UTC) - timedelta(seconds=self.lease_ttl_seconds)
        requeued: list[str] = []
        # (agent_id, workspace_id) pairs whose execution left the claimed
        # state in this sweep, for the status panel.
        released: list[tuple[str, str]] = []
        with write_transaction(self.database_dsn) as conn:
            rows = conn.execute(
                "select * from agent_execution_requests"
                " where state='claimed' and heartbeat_at<? for update skip locked",
                (cutoff,),
            ).fetchall()
            for row in rows:
                lease_id = row["lease_id"]
                node_run_id = row["node_run_id"]
                lease = conn.execute(
                    "select status from executor_leases where id=?", (lease_id,)
                ).fetchone()
                if lease is not None and lease["status"] != "active":
                    # The result path already released this lease (finish
                    # committed, mark_done lost the race or the process
                    # crashed between the two commits) — the request is owned
                    # by that path now. Close it so it cannot hold Worker
                    # capacity as a claimed zombie; never requeue a
                    # just-completed node.
                    conn.execute(
                        "update agent_execution_requests set state='done',"
                        " finished_at=current_timestamp"
                        " where execution_id=? and state='claimed'",
                        (row["execution_id"],),
                    )
                    released.append((str(row["agent_id"]), str(row["workspace_id"])))
                    continue
                if lease is None:
                    continue
                conn.execute("delete from executor_leases where id=?", (lease_id,))
                conn.execute(
                    "update node_runs set status='failed', finished_at=current_timestamp,"
                    " error_message='Agent Worker heartbeat expired' where id=?",
                    (node_run_id,),
                )
                released.append((str(row["agent_id"]), str(row["workspace_id"])))
                if int(row["attempt"]) <= self.requeue_limit:
                    reset = conn.execute(
                        "update job_nodes set status='pending', started_at=null,"
                        " finished_at=null, error_message=''"
                        " where job_id=? and node_key=? and status in ('running', 'failed')",
                        (row["job_id"], row["node_key"]),
                    )
                    if reset.rowcount == 0:
                        # Node reached a terminal state outside the broker;
                        # requeueing would double-execute it.
                        self._cancel_request(conn, row["execution_id"])
                        continue
                    conn.execute(
                        "update agent_execution_requests set state='queued', worker_id=null,"
                        " lease_id=null, node_run_id=null, claimed_at=null, heartbeat_at=null"
                        " where execution_id=?",
                        (row["execution_id"],),
                    )
                    requeued.append(str(row["execution_id"]))
                else:
                    outcome = {
                        "status": "failed",
                        "exit_code": 1,
                        "error_message": "Agent Worker heartbeat expired; requeue limit exceeded",
                    }
                    conn.execute(
                        "update agent_execution_requests set state='done', outcome_json=?,"
                        " finished_at=current_timestamp where execution_id=?",
                        (json.dumps(outcome), row["execution_id"]),
                    )
                    conn.execute(
                        "update job_nodes set status='failed', finished_at=current_timestamp,"
                        " error_message=? where job_id=? and node_key=?",
                        (outcome["error_message"], row["job_id"], row["node_key"]),
                    )
                    conn.execute(
                        "update jobs set status='failed', error_message=?, updated_at=current_timestamp"
                        " where id=?",
                        (outcome["error_message"], row["job_id"]),
                    )
        for agent_id, workspace_id in released:
            self._notify_agent_released(agent_id, workspace_id)
        return requeued

    def fail_stale_definition_requests(self) -> list[str]:
        """Fail queued requests whose pinned Agent definition is gone or disabled.

        The claim query joins the CURRENT enabled definition hash, so a request
        pinned to an edited/disabled definition would otherwise sit queued
        forever while ``has_active_request`` blocks re-enqueue."""
        failed: list[str] = []
        with write_transaction(self.database_dsn) as conn:
            rows = conn.execute(
                """
                select r.execution_id, r.job_id, r.node_key, r.agent_id
                from agent_execution_requests r
                where r.state='queued'
                  and not exists (
                      select 1 from agent_definitions d
                      where d.agent_id=r.agent_id
                        and d.definition_hash=r.agent_definition_hash
                        and d.enabled=1
                  )
                for update of r skip locked
                """
            ).fetchall()
            for row in rows:
                error = (
                    f"Agent definition {row['agent_id']!r} was disabled or changed"
                    " while the request was queued"
                )
                outcome = {"status": "failed", "exit_code": 1, "error_message": error}
                conn.execute(
                    "update agent_execution_requests set state='done', outcome_json=?,"
                    " finished_at=current_timestamp where execution_id=?",
                    (json.dumps(outcome), row["execution_id"]),
                )
                updated = conn.execute(
                    "update job_nodes set status='failed', finished_at=current_timestamp,"
                    " error_message=? where job_id=? and node_key=?"
                    " and status in ('pending', 'ready', 'stale')",
                    (error, row["job_id"], row["node_key"]),
                )
                if updated.rowcount:
                    conn.execute(
                        "update jobs set status='failed', error_message=?,"
                        " updated_at=current_timestamp"
                        " where id=? and status not in ('failed', 'completed')",
                        (error, row["job_id"]),
                    )
                failed.append(str(row["execution_id"]))
        return failed

    def reap_terminal_bundles(self, *, archive_max_age_seconds: float = 3600) -> int:
        """Reclaim bundle-dir files that no live execution can still need.

        The result route only deletes the shared execution bundle after a
        fully committed result, so failure paths (409/500, crashes) leave
        bundles of terminal requests and orphaned per-attempt result archives
        behind. This is the GC half of that contract."""
        if self.bundle_dir is None:
            return 0
        reaped = 0
        with read_connection(self.database_dsn) as conn:
            rows = conn.execute(
                "select manifest_json from agent_execution_requests"
                " where state in ('done', 'cancelled')"
            ).fetchall()
        for row in rows:
            try:
                manifest = json.loads(row["manifest_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            bundle_name = str(manifest.get("bundle_name", ""))
            if _SAFE_BUNDLE_NAME.fullmatch(bundle_name):
                target = self.bundle_dir / bundle_name
                if target.is_file():
                    target.unlink(missing_ok=True)
                    reaped += 1
        cutoff = time.time() - archive_max_age_seconds
        for orphan in self.bundle_dir.glob("*.result.tar.gz"):
            try:
                if orphan.stat().st_mtime < cutoff:
                    orphan.unlink(missing_ok=True)
                    reaped += 1
            except OSError:
                continue
        return reaped

    @staticmethod
    def _touch_worker(conn: Any, worker_id: str) -> None:
        conn.execute(
            "update agent_workers set last_seen_at=current_timestamp where worker_id=?",
            (worker_id,),
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
