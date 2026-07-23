"""Periodic sweeps for the Agent execution queue.

Split out of ``agent_broker.py`` so the broker module only carries the queue
protocol; mirrors the ``executors/_lease_*.py`` layout. Each function opens
its own transaction and takes the broker instance as its first argument.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from server.app._agent_broker_claim import cancel_request
from server.app.db.transaction import write_transaction

if TYPE_CHECKING:
    from server.app.agent_broker import AgentExecutionBroker


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
                released.append((str(row["worker_id"]), str(row["workspace_id"])))
                continue
            if lease is None:
                continue
            conn.execute("delete from executor_leases where id=?", (lease_id,))
            conn.execute(
                "update node_runs set status='failed', finished_at=current_timestamp,"
                " error_message='Agent Worker heartbeat expired' where id=?",
                (node_run_id,),
            )
            released.append((str(row["worker_id"]), str(row["workspace_id"])))
            if int(row["attempt"]) <= broker.requeue_limit:
                reset = conn.execute(
                    "update job_nodes set status='pending', started_at=null,"
                    " finished_at=null, error_message=''"
                    " where job_id=? and node_key=? and status in ('running', 'failed')",
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
