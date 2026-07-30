"""PostgreSQL Agent queue protocol: enqueue, claim, heartbeat, result close.

The claim transaction lives in ``_agent_broker_claim.py``, the periodic
sweeps in ``_agent_broker_sweepers.py``, slot release in
``_agent_broker_release.py``, bundle-dir GC in ``_agent_broker_reaper.py``.
"""

from __future__ import annotations

import itertools
import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from psycopg import IntegrityError

from server.app import _agent_broker_reaper, _agent_broker_release, _agent_broker_sweepers
from server.app._agent_broker_claim import (
    AgentClaim,
    ClaimRacedError,
    claim_in_transaction,
)
from server.app._agent_broker_reaper import _SAFE_BUNDLE_NAME
from server.app.agent_worker_capacity import touch_worker
from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction
from server.app.job_events import record_job_update

if TYPE_CHECKING:
    from server.app.agents import AgentStatusManager
    from server.app.jobs import JobQueries

_ACTIVE_LEASE_CONSTRAINT = "idx_agent_requests_one_active_node"


@dataclass(frozen=True)
class AgentExecutionRequest:
    workspace_id: str
    job_id: str
    workflow_key: str
    node_key: str
    agent_id: str
    agent_definition_hash: str
    manifest: Mapping[str, Any]
    execution_id: str = ""


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
        is_workspace_paused: Callable[[str], bool] | None = None,
        job_db: JobQueries | None = None,
        job_event_buffer: Any | None = None,
    ) -> None:
        self.database_dsn = database_dsn
        self.lease_ttl_seconds = lease_ttl_seconds
        self.bundle_dir = bundle_dir
        self.requeue_limit = requeue_limit
        # Optional status-panel sink: when wired, claim/finish transitions are
        # mirrored into the /api/agents feed so the workspace panel shows
        # distributed executions, not only in-process runners.
        self.agent_status = agent_status
        # Workspace pause must gate the pull side too: queued requests of a
        # paused workspace stay queued (never claimed) until resume, matching
        # the job-level pause re-check below.
        self.is_workspace_paused = is_workspace_paused
        # Live job-list events: claim promotes jobs queued -> running, so the
        # broker must record updates just like the lease finish path does;
        # otherwise filtered views only shrink, never grow.
        self.job_db = job_db
        self.job_event_buffer = job_event_buffer
        # Rotating cursor for bounded cross-workspace fairness (EXEC-FAIRNESS
        # style): each claim pass starts candidate evaluation at the next
        # workspace instead of always at the globally oldest request.
        self._fairness_counter = itertools.count()
        # Incremental bundle-GC cursor, see _agent_broker_reaper.
        self._reap_watermark: datetime | None = None

    def has_active_request(self, job_id: str, node_key: str) -> bool:
        with read_connection(self.database_dsn) as conn:
            row = conn.execute(
                "select 1 from agent_execution_requests"
                " where job_id=? and node_key=? and state in ('queued', 'claimed', 'reporting') limit 1",
                (job_id, node_key),
            ).fetchone()
        return row is not None

    def enqueue(self, request: AgentExecutionRequest) -> str | None:
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
                # Audit-only snapshot of the governing workspace-level limit
                # at enqueue time; 1 records "no configured limit
                # (unlimited)". Never used for enforcement.
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

    def claim(
        self, worker_id: str, declared_max_concurrency: int | None = None
    ) -> AgentClaim | None:
        try:
            with write_transaction(self.database_dsn) as conn:
                claimed = claim_in_transaction(self, conn, worker_id, declared_max_concurrency)
        except ClaimRacedError:
            return None
        # Record only after the commit has succeeded, never inside the tx.
        if claimed is not None:
            record_job_update(self.job_db, self.job_event_buffer, claimed.job_id)
        self._notify_worker_poll(worker_id, claimed)
        return claimed

    def _notify_worker_poll(self, worker_id: str, claimed: AgentClaim | None) -> None:
        """Mirror Worker presence/capacity into the status panel.

        An idle Worker polls claim every few seconds, so every poll registers
        one panel row per workspace the Worker may serve ("name busy/cap");
        a successful claim additionally marks the row busy."""
        manager = self.agent_status
        if manager is None:
            return
        with read_connection(self.database_dsn) as conn:
            worker = conn.execute(
                "select name, max_concurrency, allowed_workspaces_json from agent_workers"
                " where worker_id=?",
                (worker_id,),
            ).fetchone()
            if worker is None:
                return
            allowed = set(json.loads(worker["allowed_workspaces_json"] or "[]"))
            if allowed:
                workspace_ids = sorted(allowed)
            else:
                rows = conn.execute("select id from workspaces").fetchall()
                workspace_ids = [str(row["id"]) for row in rows]
        max_tasks = int(worker["max_concurrency"])
        name = str(worker["name"] or worker_id)
        for workspace_id in workspace_ids:
            manager.ensure_workspace_agent(worker_id, workspace_id, max_tasks=max_tasks, name=name)
        if claimed is not None:
            manager.set_busy(worker_id, "", workspace_id=claimed.workspace_id)

    def _notify_worker_released(self, worker_id: str, workspace_id: str) -> None:
        if self.agent_status is not None:
            self.agent_status.set_idle(worker_id, workspace_id=workspace_id)

    def release_slot(self, execution_id: str, worker_id: str, lease_id: str) -> bool:
        """Flip claimed -> reporting, freeing execution capacity (lease stays owned)."""
        return _agent_broker_release.release_slot(self, execution_id, worker_id, lease_id)

    def heartbeat(self, execution_id: str, worker_id: str, lease_id: str) -> bool:
        """Renew the lease; bound to the current lease_id so zombie attempts
        from a requeued execution cannot keep a re-claimed lease alive.

        Route note (routes owner): the heartbeat endpoint must accept the
        worker's current lease_id and pass it through."""
        expires_at = datetime.now(UTC) + timedelta(seconds=self.lease_ttl_seconds)
        with write_transaction(self.database_dsn) as conn:
            row = conn.execute(
                "select lease_id from agent_execution_requests"
                " where execution_id=? and worker_id=? and lease_id=?"
                " and state in ('claimed', 'reporting')"
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
            touch_worker(conn, worker_id)
            return True

    def claimed_payload(self, execution_id: str, worker_id: str) -> dict[str, Any] | None:
        with read_connection(self.database_dsn) as conn:
            row = conn.execute(
                "select manifest_json, lease_id, job_id, node_key from agent_execution_requests"
                " where execution_id=? and worker_id=? and state in ('claimed', 'reporting')",
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
                " where execution_id=? and worker_id=? and lease_id=?"
                " and state in ('claimed', 'reporting')"
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
            touch_worker(conn, worker_id)
        self._notify_worker_released(worker_id, str(row["workspace_id"]))
        return str(row["lease_id"])

    def sweep_expired_claims(self) -> list[str]:
        """Requeue Worker-lost claims without leaving the workflow node running."""
        return _agent_broker_sweepers.sweep_expired_claims(self)

    def fail_stale_definition_requests(self) -> list[str]:
        """Fail queued requests whose pinned Agent definition is gone or disabled."""
        return _agent_broker_sweepers.fail_stale_definition_requests(self)

    def discard_result_archive(self, archive_name: str) -> None:
        """Reclaim a per-attempt result archive; names are unique per attempt."""
        if self.bundle_dir is not None:
            (self.bundle_dir / archive_name).unlink(missing_ok=True)

    def retire_bundle(self, bundle_name: str) -> None:
        """Retire the shared execution bundle after a fully committed result."""
        if self.bundle_dir is not None and _SAFE_BUNDLE_NAME.fullmatch(bundle_name):
            (self.bundle_dir / bundle_name).unlink(missing_ok=True)

    def reap_terminal_bundles(self, *, archive_max_age_seconds: float = 3600) -> int:
        """Reclaim bundle-dir files that no live execution can still need."""
        return _agent_broker_reaper.reap_terminal_bundles(
            self, archive_max_age_seconds=archive_max_age_seconds
        )
