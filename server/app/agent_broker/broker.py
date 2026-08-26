"""PostgreSQL Agent queue protocol: enqueue, claim, heartbeat, result close.

The claim transaction lives in ``claim.py``, enqueue in ``enqueue.py``, the
periodic sweeps in ``sweepers.py``, slot release in
``release.py``, bundle-dir GC in ``reaper.py``.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.agent_broker import reaper, release, sweepers
from server.app.agent_broker.agent_worker_capacity import touch_worker
from server.app.agent_broker.claim import (
    AgentClaim,
    ClaimRacedError,
    claim_in_transaction,
)
from server.app.agent_broker.code_manifest import CODE_MANIFEST_TRIM
from server.app.agent_broker.empty import EmptyClaimTrigger
from server.app.agent_broker.enqueue import enqueue_request
from server.app.agent_broker.reaper import _SAFE_BUNDLE_NAME
from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction
from server.app.events.aggregator import record_job_update
from server.app.executors._lease_lifecycle import heartbeat_lease

if TYPE_CHECKING:
    from server.app.events.agents import AgentStatusManager
    from server.app.jobs import JobQueries


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
    # Quality replay (schema v29): pin an immutable Agent version instead of
    # the currently published one; enqueue/claim/sweep match by version+hash.
    pinned_agent_version: int | None = None
    # 'agent' (default) or 'code' (batch 2): code rows skip the Agent-route
    # and versioned_entities validation — their payload is self-contained
    # (code text + hash ride the bundle) and the dispatch path already
    # validated the executor binding and worker eligibility.
    kind: str = "agent"


class AgentExecutionBroker:
    """PostgreSQL Agent queue with atomic node and Worker capacity enforcement."""

    def __init__(
        self,
        database_dsn: DatabaseDsn,
        *,
        lease_ttl_seconds: int = 90,
        bundle_dir: Path | None = None,
        data_dir: Path,
        requeue_limit: int = 3,
        agent_status: AgentStatusManager | None = None,
        is_workspace_paused: Callable[[str], bool] | None = None,
        job_db: JobQueries | None = None,
        job_event_buffer: Any | None = None,
    ) -> None:
        self.database_dsn = database_dsn
        self.lease_ttl_seconds = lease_ttl_seconds
        self.bundle_dir = bundle_dir
        self.data_dir = data_dir
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
        # Incremental bundle-GC cursor, see reaper.
        self._reap_watermark: datetime | None = None
        # Debounced empty-claim restock signal, see empty.
        self.empty_claim = EmptyClaimTrigger()

    def has_active_request(self, job_id: str, node_key: str) -> bool:
        with read_connection(self.database_dsn) as conn:
            row = conn.execute(
                "select 1 from agent_execution_requests"
                " where job_id=%s and node_key=%s and state in ('queued', 'claimed', 'reporting') limit 1",
                (job_id, node_key),
            ).fetchone()
        return row is not None

    def enqueue(self, request: AgentExecutionRequest) -> str | None:
        """Insert one queued request; None when the node has an active one.

        The transaction lives in ``enqueue.py`` (file-size budget).
        """
        return enqueue_request(self, request)

    def claim(
        self,
        worker_id: str,
        declared_max_concurrency: int | None = None,
        declared_max_code_concurrency: int | None = None,
    ) -> AgentClaim | None:
        try:
            with write_transaction(self.database_dsn) as conn:
                claimed, skip_reasons = claim_in_transaction(
                    self, conn, worker_id, declared_max_concurrency, declared_max_code_concurrency
                )
        except ClaimRacedError:
            return None
        if claimed is None:
            # Demand signal: a Worker found no work; restock immediately when
            # the queue is truly empty, or surface the skip-reason histogram
            # when unclaimable stock blocked the claim (debounced, see empty).
            self.empty_claim.note_empty_claim(self.database_dsn, skip_reasons=skip_reasons)
        # Record only after the commit has succeeded, never inside the tx.
        if claimed is not None:
            record_job_update(self.job_db, self.job_event_buffer, claimed.job_id)
        self._notify_worker_poll(worker_id, claimed)
        return claimed

    def _notify_worker_poll(self, worker_id: str, claimed: AgentClaim | None) -> None:
        """Mirror Worker presence/capacity into the status panel: every idle
        poll registers one row per servable workspace; a claim marks busy."""
        manager = self.agent_status
        if manager is None:
            return
        with read_connection(self.database_dsn) as conn:
            worker = conn.execute(
                "select name, max_concurrency, allowed_workspaces_json from agent_workers"
                " where worker_id=%s",
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
            manager.set_busy(worker_id, workspace_id=claimed.workspace_id)

    def _notify_worker_released(self, worker_id: str, workspace_id: str) -> None:
        if self.agent_status is not None:
            self.agent_status.set_idle(worker_id, workspace_id=workspace_id)

    def release_slot(self, execution_id: str, worker_id: str, lease_id: str) -> bool:
        """Flip claimed -> reporting, freeing execution capacity (lease stays owned)."""
        return release.release_slot(self, execution_id, worker_id, lease_id)

    def heartbeat(self, execution_id: str, worker_id: str, lease_id: str) -> bool:
        """Renew the lease, bound to the current lease_id so zombie attempts
        from a requeued execution cannot keep a re-claimed lease alive."""
        with write_transaction(self.database_dsn) as conn:
            row = conn.execute(
                "select lease_id from agent_execution_requests"
                " where execution_id=%s and worker_id=%s and lease_id=%s"
                " and state in ('claimed', 'reporting')"
                " for update",
                (execution_id, worker_id, lease_id),
            ).fetchone()
            if row is None:
                return False
            conn.execute(
                "update agent_execution_requests set heartbeat_at=current_timestamp"
                " where execution_id=%s",
                (execution_id,),
            )
            if not heartbeat_lease(conn, row["lease_id"], self.lease_ttl_seconds):
                # Released concurrently: success would keep a zombie attempt alive.
                return False
            touch_worker(conn, worker_id)
            return True

    def claimed_payload(self, execution_id: str, worker_id: str) -> dict[str, Any] | None:
        with read_connection(self.database_dsn) as conn:
            row = conn.execute(
                "select manifest_json, lease_id, job_id, node_key from agent_execution_requests"
                " where execution_id=%s and worker_id=%s and state in ('claimed', 'reporting')",
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

    def cancelled_code_executions(self, worker_id: str) -> list[str]:
        """Claimed kind='code' executions whose job left the runnable set.

        Backs the heartbeat cancel body (batch 2, design §7.5): the Worker
        kills the process group for each returned execution_id. Only
        'claimed' rows matter — 'reporting' executions already exited.
        """
        with read_connection(self.database_dsn) as conn:
            rows = conn.execute(
                "select r.execution_id from agent_execution_requests r"
                " join jobs j on j.id=r.job_id"
                " where r.worker_id=%s and r.kind='code' and r.state='claimed'"
                " and (j.execution_paused=1 or j.status not in ('queued', 'running'))",
                (worker_id,),
            ).fetchall()
        return [str(row["execution_id"]) for row in rows]

    def mark_done(
        self, execution_id: str, worker_id: str, lease_id: str, outcome: Mapping[str, Any]
    ) -> str | None:
        """Close the request; bound to the current lease_id so a late result
        from a previous attempt is rejected after a requeue/re-claim."""
        with write_transaction(self.database_dsn) as conn:
            row = conn.execute(
                "select lease_id, agent_id, workspace_id from agent_execution_requests"
                " where execution_id=%s and worker_id=%s and lease_id=%s"
                " and state in ('claimed', 'reporting')"
                " for update",
                (execution_id, worker_id, lease_id),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "update agent_execution_requests set state='done', outcome_json=%s,"
                " finished_at=current_timestamp, manifest_json="
                + CODE_MANIFEST_TRIM
                + " where execution_id=%s",
                (json.dumps(dict(outcome), ensure_ascii=False), execution_id),
            )
            touch_worker(conn, worker_id)
        self._notify_worker_released(worker_id, str(row["workspace_id"]))
        return str(row["lease_id"])

    def sweep_expired_claims(self) -> list[str]:
        """Requeue Worker-lost claims without leaving the workflow node running."""
        return sweepers.sweep_expired_claims(self)

    def fail_stale_definition_requests(self) -> list[str]:
        """Fail queued requests whose pinned Agent definition is gone or disabled."""
        return sweepers.fail_stale_definition_requests(self)

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
        return reaper.reap_terminal_bundles(self, archive_max_age_seconds=archive_max_age_seconds)
