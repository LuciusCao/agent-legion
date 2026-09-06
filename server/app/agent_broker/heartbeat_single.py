"""Single-execution lease renewal for the Agent Worker data plane.

Split from ``broker.py`` (#490 rebase onto 0.7.0): broker.py sits at its
#401 frozen ceiling and the heartbeat's #490 refusal events could not ride
its two refusal exits in place. The renewal predicate is identical to the
batch path's per-item renewal (``heartbeat_batch._renew_one``) — the row
must be this Worker's, under this exact lease_id, still claimable — and a
refusal emits ``execution.heartbeat_rejected`` (#490) with the refusal
reason, the Host-side clue that tells the Worker to stop beating.

#499 discipline (shared with the batch path): the refusal reason is decided
inside the write transaction but the EVENT is emitted only after the commit
succeeds — a refusal verdict reached inside a transaction that then fails
(commit loss, deadlock, connection reset) never happened, and an event for
it would be the same ghost the claim path fixed in #498.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from server.app.agent_broker.agent_worker_capacity import touch_worker
from server.app.agent_broker.worker_events import (
    HEARTBEAT_LEASE_NOT_ACTIVE,
    HEARTBEAT_NOT_OWNED,
    note_heartbeat_rejected,
)
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
    # The refusal reason is collected here, emitted after the commit: the
    # renewal and its verdict must land before the event stream learns of it.
    reason = _renew_in_transaction(broker, execution_id, worker_id, lease_id)
    if reason is None:
        return True
    note_heartbeat_rejected(execution_id, worker_id, reason)
    return False


def _renew_in_transaction(
    broker: AgentExecutionBroker, execution_id: str, worker_id: str, lease_id: str
) -> str | None:
    """One renewal attempt; None = renewed, else the refusal reason
    (shared literals, see worker_events.HEARTBEAT_*)."""
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
            return HEARTBEAT_NOT_OWNED
        conn.execute(
            "update agent_execution_requests set heartbeat_at=current_timestamp"
            " where execution_id=%s",
            (execution_id,),
        )
        if not heartbeat_lease(conn, row["lease_id"], broker.lease_ttl_seconds):
            # Released concurrently: success would keep a zombie attempt alive.
            return HEARTBEAT_LEASE_NOT_ACTIVE
        touch_worker(conn, worker_id)
        return None
