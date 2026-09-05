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
from server.app.agent_broker.manifest_trim import MANIFEST_TRIM
from server.app.agent_broker.worker_events import note_lease_expired
from server.app.db.transaction import write_transaction
from server.app.workflows.sharding_requeue import (
    fail_shard_for_dead_execution,
    reset_shard_for_requeue,
)

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker

logger = logging.getLogger(__name__)

# Shared terminal 'done' write for the sweep paths: closes the request and
# slims the manifest back to the audit stub in the same statement
# (MANIFEST_TRIM per kind: code #142, agent #354).
_SWEEP_DONE_SQL = (
    "update agent_execution_requests set state='done', outcome_json=%s,"
    " finished_at=current_timestamp, manifest_json=" + MANIFEST_TRIM + " where execution_id=%s"
)


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
                    " finished_at=current_timestamp, manifest_json="
                    + MANIFEST_TRIM
                    + " where execution_id=%s and state in ('claimed', 'reporting')",
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
            note_lease_expired(row, broker.requeue_limit)
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
                # Shard-aware requeue (#389 review P1-1): reset the bound
                # node_shards row in the same transaction (sharding_requeue)
                # — a stranded 'running' row would make every later claim
                # reject the shard forever.
                reset_shard_for_requeue(conn, row["job_id"], row["node_key"], row["execution_id"])
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
                conn.execute(_SWEEP_DONE_SQL, (json.dumps(outcome), row["execution_id"]))
                # Shard-aware terminal path (#389 review P1-1): fail the
                # stranded shard through the aggregate (sharding_requeue) —
                # on_shard_finished decides whether the whole node fails.
                fail_shard_for_dead_execution(
                    conn, row["job_id"], row["node_key"], row["execution_id"], error_message
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
    if requeued:
        # Runtime profile (#359): requeue-rate gauge (lease/worker-loss
        # signal the classifier pairs with heartbeat latency).
        from server.app.services.runtime_profile import profile

        profile.note_execution_requeued(len(requeued))
    # Force-closed rows (requeue limit exceeded) are terminal executions too:
    # the done-rate gauge must not undercount exactly when workers are lost
    # en masse (independent-review P2 on #367).
    if len(requeued) < len(rows):
        from server.app.services.runtime_profile import profile

        profile.note_execution_done(len(rows) - len(requeued))
    return requeued
