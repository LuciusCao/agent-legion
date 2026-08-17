"""Periodic sweeps for the Agent execution queue.

Split out of ``broker.py`` so the broker module only carries the queue
protocol; mirrors the ``executors/_lease_*.py`` layout. Each function opens
its own transaction and takes the broker instance as its first argument.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from server.app.agent_broker.claim import cancel_request
from server.app.db.transaction import write_transaction
from server.app.executors._failed_node_recording import record_failed_node_without_execution
from server.app.services import failure_classification

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker

logger = logging.getLogger(__name__)


def sweep_expired_claims(broker: AgentExecutionBroker) -> list[str]:
    """Requeue Worker-lost claims without leaving the workflow node running."""
    cutoff = datetime.now(UTC) - timedelta(seconds=broker.lease_ttl_seconds)
    requeued: list[str] = []
    # (worker_id, workspace_id) pairs whose execution left the claimed
    # state in this sweep, for the status panel.
    released: list[tuple[str, str]] = []
    with write_transaction(broker.database_dsn) as conn:
        rows = conn.execute(
            "select * from agent_execution_requests"
            " where state in ('claimed', 'reporting') and heartbeat_at<%s"
            " for update skip locked",
            (cutoff,),
        ).fetchall()
        for row in rows:
            lease_id = row["lease_id"]
            node_run_id = row["node_run_id"]
            lease = conn.execute(
                "select status from executor_leases where id=%s", (lease_id,)
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
                    " where execution_id=%s and state in ('claimed', 'reporting')",
                    (row["execution_id"],),
                )
                released.append((str(row["worker_id"]), str(row["workspace_id"])))
                continue
            if lease is None:
                continue
            # Sole lease-deletion path in the repo: audit the Worker-loss sweep.
            logger.warning(
                f"deleting expired agent lease {lease_id} exec={row['execution_id']}"
                f" job={row['job_id']} worker={row['worker_id']} attempt={row['attempt']}"
            )
            conn.execute("delete from executor_leases where id=%s", (lease_id,))
            conn.execute(
                "update node_runs set status='failed', finished_at=current_timestamp,"
                " error_message='Agent Worker heartbeat expired' where id=%s",
                (node_run_id,),
            )
            released.append((str(row["worker_id"]), str(row["workspace_id"])))
            if int(row["attempt"]) <= broker.requeue_limit:
                reset = conn.execute(
                    "update job_nodes set status='pending', started_at=null,"
                    " finished_at=null, error_message=''"
                    " where job_id=%s and node_key=%s and status in ('running', 'failed')",
                    (row["job_id"], row["node_key"]),
                )
                if reset.rowcount == 0:
                    # Node reached a terminal state outside the broker;
                    # requeueing would double-execute it.
                    cancel_request(conn, row["execution_id"])
                    continue
                conn.execute(
                    "update agent_execution_requests set state='queued', worker_id=null,"
                    " lease_id=null, node_run_id=null, claimed_at=null, heartbeat_at=null"
                    " where execution_id=%s",
                    (row["execution_id"],),
                )
                requeued.append(str(row["execution_id"]))
            else:
                error_message = (
                    "Agent Worker heartbeat expired; requeue limit exceeded"
                    f" (execution={row['execution_id']} worker={row['worker_id']}"
                    f" attempt={row['attempt']} limit={broker.requeue_limit})"
                )
                outcome = {"status": "failed", "exit_code": 1, "error_message": error_message}
                conn.execute(
                    "update agent_execution_requests set state='done', outcome_json=%s,"
                    " finished_at=current_timestamp where execution_id=%s",
                    (json.dumps(outcome), row["execution_id"]),
                )
                conn.execute(
                    "update job_nodes set status='failed', finished_at=current_timestamp,"
                    " error_message=%s where job_id=%s and node_key=%s",
                    (outcome["error_message"], row["job_id"], row["node_key"]),
                )
                conn.execute(
                    "update jobs set status='failed', error_message=%s, updated_at=current_timestamp"
                    " where id=%s",
                    (outcome["error_message"], row["job_id"]),
                )
    for worker_id, workspace_id in released:
        broker._notify_worker_released(worker_id, workspace_id)
    return requeued


def fail_stale_definition_requests(broker: AgentExecutionBroker) -> list[str]:
    """Fail queued requests whose pinned Agent definition is gone or disabled.

    The claim query joins the CURRENT enabled definition hash, so a request
    pinned to an edited/disabled definition would otherwise sit queued
    forever while ``has_active_request`` blocks re-enqueue."""
    failed: list[str] = []
    with write_transaction(broker.database_dsn) as conn:
        rows = conn.execute(
            """
            select r.execution_id, r.job_id, r.node_key, r.agent_id
            from agent_execution_requests r
            where r.state='queued'
              -- kind='code' payloads are self-contained: no versioned Agent
              -- definition exists for them by design (batch 2).
              and r.kind='agent'
              and not exists (
                  select 1 from versioned_entities d
                  where d.entity_type='agent' and d.workspace_id=r.workspace_id
                    and d.entity_key=r.agent_id
                    and d.definition_hash=r.agent_definition_hash
                    -- Quality replay pins stay valid while their immutable
                    -- version row exists, whatever its lifecycle status.
                    and ((r.pinned_agent_version is not null
                          and d.version=r.pinned_agent_version)
                         or (r.pinned_agent_version is null and d.status='published'))
              )
            for update of r skip locked
            """
        ).fetchall()
        for row in rows:
            error = (
                f"Agent definition {row['agent_id']!r} was disabled or changed"
                " while the request was queued"
            )
            failure_category, failure_detail = failure_classification.resolve_failure_fields(
                "failed", None, error
            )
            outcome = {"status": "failed", "exit_code": 1, "error_message": error}
            conn.execute(
                "update agent_execution_requests set state='done', outcome_json=%s,"
                " finished_at=current_timestamp where execution_id=%s",
                (json.dumps(outcome), row["execution_id"]),
            )
            updated = record_failed_node_without_execution(
                conn,
                job_id=str(row["job_id"]),
                node_key=str(row["node_key"]),
                error_message=error,
                failure_category=failure_category,
                failure_detail=failure_detail,
            )
            if updated is not None:
                conn.execute(
                    "update jobs set status='failed', error_message=%s,"
                    " updated_at=current_timestamp"
                    " where id=%s and status not in ('failed', 'completed')",
                    (error, row["job_id"]),
                )
            failed.append(str(row["execution_id"]))
    return failed
