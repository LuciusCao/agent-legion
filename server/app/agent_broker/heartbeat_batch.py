"""Per-Worker batch lease renewal (protocol v5, #352).

One request renews every claimed lease of one Worker in a single write
transaction — the heartbeat write load stops scaling with the fleet's slot
count and scales with the machine counts instead. Semantics per batch item are
identical to the single-execution heartbeat (``broker.heartbeat``): row lock,
``heartbeat_at`` refresh, lease renewal bound to the current lease_id, and a
Worker liveness touch. Unknown/expired items are reported per execution (the
Worker prunes them locally) instead of failing the request, so one stale item
never blocks the renewal of its batch siblings.

#499: every lost item also emits ``execution.heartbeat_rejected`` with the
same reason literals the single path uses (``worker_events.HEARTBEAT_*``),
and the emissions happen only after the batch transaction commits — the
#498 post-commit discipline (a verdict from a rolled-back transaction never
happened).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.agent_broker.agent_worker_capacity import touch_worker
from server.app.agent_broker.worker_events import (
    HEARTBEAT_LEASE_NOT_ACTIVE,
    HEARTBEAT_NOT_OWNED,
    note_heartbeat_rejected,
)
from server.app.db.transaction import write_transaction

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker

# One batch = one write transaction; an unbounded batch would recreate the
# long-transaction problem this split exists to solve. One renewal is a fixed
# handful of cheap primary-key statements, and a Worker's live claims are
# bounded by max_concurrency + max_code_concurrency (registration caps both at
# 1024), so 256 items per transaction keeps the worst case in the tens-of-
# milliseconds range while covering every realistic slot count in one round.
MAX_BATCH_HEARTBEATS = 256


def _renew_one(
    conn: Any, broker: AgentExecutionBroker, worker_id: str, execution_id: str, lease_id: str
) -> str | None:
    """Renew one lease inside the batch transaction; None = renewed, else the
    refusal reason (HEARTBEAT_NOT_OWNED / HEARTBEAT_LEASE_NOT_ACTIVE).

    The single heartbeat's exact predicate — the row must be this Worker's,
    under this exact lease_id, still claimable — so zombie attempts from a
    requeued execution cannot keep a re-claimed lease alive, and one Worker
    can never renew another's execution. The reason literals are shared with
    the single path (#499): both failure points must classify identically."""
    row = conn.execute(
        "select lease_id from agent_execution_requests"
        " where execution_id=%s and worker_id=%s and lease_id=%s"
        " and state in ('claimed', 'reporting')"
        " for update",
        (execution_id, worker_id, lease_id),
    ).fetchone()
    if row is None:
        return HEARTBEAT_NOT_OWNED
    conn.execute(
        "update agent_execution_requests set heartbeat_at=current_timestamp where execution_id=%s",
        (execution_id,),
    )
    from server.app.executors._lease_lifecycle import heartbeat_lease

    # Released concurrently: success would keep a zombie attempt alive.
    return (
        None
        if heartbeat_lease(conn, str(row["lease_id"]), broker.lease_ttl_seconds)
        else (HEARTBEAT_LEASE_NOT_ACTIVE)
    )


def batch_heartbeat(
    broker: AgentExecutionBroker, worker_id: str, items: list[dict[str, str]]
) -> dict[str, Any]:
    """Renew a batch of ``[{'execution_id', 'lease_id'}, ...]`` for one Worker.

    Returns ``{'renewed': [...], 'lost': [...]}`` (execution ids). An item is
    lost when this Worker no longer owns the execution under that exact lease
    (unknown id, swept/requeued lease, wrong worker) — the same 409 family the
    single heartbeat reports, surfaced per item so the rest of the batch still
    renews. Duplicated execution ids are collapsed to the last lease_id.

    Lock-order note (#5125358408 P1-B): this transaction locks THREE tables,
    always in this order — agent_execution_requests (``for update`` per batch
    item, iterated in sorted execution_id order), then executor_leases
    (``heartbeat_lease``'s select+update by primary key), then agent_workers
    (``touch_worker``'s row update). The AER→lease ordering matches every
    other renewal path (single heartbeat, claim promote, result commit), so
    cross-table cycles would need a path that locks leases BEFORE requests —
    none exists today and any such edit must re-prove this note. The
    per-item AER row locks themselves are taken in sorted (primary-key) order
    so two concurrent batch transactions sharing one worker registration
    (an ops-incident scenario) lock the same rows in the same order instead
    of interleaving A,B / B,A — the deadlock face v5 introduced. Any future
    edit adding a fourth locked table must extend this note with its
    position and the same-direction argument."""
    # Collapse duplicates while preserving order: a Worker bug sending the
    # same execution twice must not lock and update one row twice.
    by_execution: dict[str, str] = {}
    for item in items:
        by_execution[str(item["execution_id"])] = str(item["lease_id"])
    renewed: list[str] = []
    lost: list[str] = []
    # Refusal reasons per lost item, emitted only after the commit (#498
    # discipline shared with the claim path): a transaction that rolls back
    # never happened, so its verdicts must not reach the event stream.
    lost_reasons: dict[str, str] = {}
    with write_transaction(broker.database_dsn) as conn:
        for execution_id, lease_id in sorted(by_execution.items()):
            reason = _renew_one(conn, broker, worker_id, execution_id, lease_id)
            if reason is None:
                renewed.append(execution_id)
            else:
                lost.append(execution_id)
                lost_reasons[execution_id] = reason
        if renewed:
            # Same liveness touch as the single heartbeat; authenticate()
            # already throttles last_seen_at to one write per 10s per Worker.
            touch_worker(conn, worker_id)
    for execution_id in lost:
        note_heartbeat_rejected(execution_id, worker_id, lost_reasons[execution_id])
    return {"renewed": renewed, "lost": lost}
