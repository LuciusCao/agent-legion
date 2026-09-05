"""Per-Worker batch lease renewal (protocol v5, #352).

One request renews every claimed lease of one Worker in a single write
transaction — the heartbeat write load stops scaling with the fleet's slot
count and scales with the machine count instead. Semantics per batch item are
identical to the single-execution heartbeat (``broker.heartbeat``): row lock,
``heartbeat_at`` refresh, lease renewal bound to the current lease_id, and a
Worker liveness touch. Unknown/expired items are reported per execution (the
Worker prunes them locally) instead of failing the request, so one stale item
never blocks the renewal of its batch siblings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.agent_broker.agent_worker_capacity import touch_worker
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
) -> bool:
    """Renew one lease inside the batch transaction; False = lost (409 family).

    The single heartbeat's exact predicate — the row must be this Worker's,
    under this exact lease_id, still claimable — so zombie attempts from a
    requeued execution cannot keep a re-claimed lease alive, and one Worker
    can never renew another's execution."""
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
        "update agent_execution_requests set heartbeat_at=current_timestamp where execution_id=%s",
        (execution_id,),
    )
    from server.app.executors._lease_lifecycle import heartbeat_lease

    # Released concurrently: success would keep a zombie attempt alive.
    return heartbeat_lease(conn, str(row["lease_id"]), broker.lease_ttl_seconds)


def batch_heartbeat(
    broker: AgentExecutionBroker, worker_id: str, items: list[dict[str, str]]
) -> dict[str, Any]:
    """Renew a batch of ``[{'execution_id', 'lease_id'}, ...]`` for one Worker.

    Returns ``{'renewed': [...], 'lost': [...]}`` (execution ids). An item is
    lost when this Worker no longer owns the execution under that exact lease
    (unknown id, swept/requeued lease, wrong worker) — the same 409 family the
    single heartbeat reports, surfaced per item so the rest of the batch still
    renews. Duplicated execution ids are collapsed to the last lease_id.

    Lock-order note: this transaction takes multiple unordered
    ``select ... for update`` row locks on agent_execution_requests (one per
    batch item). Every lock this transaction can hold is in this one table,
    so it cannot form a wait cycle with itself; any future edit that adds a
    second locked table here MUST either lock rows in a deterministic order
    (order by primary key — repo precedent register_token_deletion.py) or use
    ``for update skip locked`` to prevent deadlocks."""
    # Collapse duplicates while preserving order: a Worker bug sending the
    # same execution twice must not lock and update one row twice.
    by_execution: dict[str, str] = {}
    for item in items:
        by_execution[str(item["execution_id"])] = str(item["lease_id"])
    renewed: list[str] = []
    lost: list[str] = []
    with write_transaction(broker.database_dsn) as conn:
        for execution_id, lease_id in by_execution.items():
            if _renew_one(conn, broker, worker_id, execution_id, lease_id):
                renewed.append(execution_id)
            else:
                lost.append(execution_id)
        if renewed:
            # Same liveness touch as the single heartbeat; authenticate()
            # already throttles last_seen_at to one write per 10s per Worker.
            touch_worker(conn, worker_id)
    return {"renewed": renewed, "lost": lost}
