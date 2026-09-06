"""Single-execution lease renewal for the Agent Worker data plane.

Split from ``broker.py`` (#490 rebase onto 0.7.0): broker.py sits at its
#401 frozen ceiling and the heartbeat's #490 refusal events could not ride
its two refusal exits in place. The renewal predicate is identical to the
batch path's per-item renewal (``heartbeat_batch._renew_one``) — the row
must be this Worker's, under this exact lease_id, still claimable — and a
refusal emits ``execution.heartbeat_rejected`` (#490) with the refusal
reason, the Host-side clue that tells the Worker to stop beating.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from server.app.agent_broker.agent_worker_capacity import touch_worker
from server.app.agent_broker.worker_events import note_heartbeat_rejected
from server.app.db.transaction import write_transaction
from server.app.executors._lease_lifecycle import heartbeat_lease

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker


def single_heartbeat(
    broker: AgentExecutionBroker, execution_id: str, worker_id: str, lease_id: str
) -> bool:
    """Renew one lease in its own transaction, bound to the current lease_id
    so zombie attempts from a requeued execution cannot keep a re-claimed
    lease alive; False = refused (409 family)."""
    with write_transaction(broker.database_dsn) as conn:
        row = conn.execute(
            "select lease_id from agent_execution_requests"
            " where execution_id=%s and worker_id=%s and lease_id=%s"
            " and state in ('claimed', 'reporting')"
            " for update",
            (execution_id, worker_id, lease_id),
        ).fetchone()
        if row is None:
            # Not this Worker's under this lease: unknown id / swept /
            # requeued / wrong worker.
            note_heartbeat_rejected(execution_id, worker_id, "not_owned")
            return False
        conn.execute(
            "update agent_execution_requests set heartbeat_at=current_timestamp"
            " where execution_id=%s",
            (execution_id,),
        )
        if not heartbeat_lease(conn, row["lease_id"], broker.lease_ttl_seconds):
            # Released concurrently: success would keep a zombie attempt alive.
            note_heartbeat_rejected(execution_id, worker_id, "lease_not_active")
            return False
        touch_worker(conn, worker_id)
        return True
