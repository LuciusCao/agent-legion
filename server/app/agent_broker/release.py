"""Slot release for the Agent broker: flip claimed -> reporting at process exit.

Mirrors the ``claim.py`` / ``sweepers.py`` split:
the :class:`AgentExecutionBroker` method delegates here so the broker module
stays within its size budget.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from server.app.db.transaction import write_transaction

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker


def release_slot(
    broker: AgentExecutionBroker, execution_id: str, worker_id: str, lease_id: str
) -> bool:
    """Flip claimed -> reporting: the Agent process exited and only the
    result upload remains. Capacity accounting counts only 'claimed', so
    this releases the Worker/workspace execution slot immediately while
    the lease stays owned (heartbeat + mark_done accept 'reporting').
    Bound to the current lease_id so a stale attempt cannot flip a
    re-claimed request."""
    with write_transaction(broker.database_dsn) as conn:
        updated = conn.execute(
            "update agent_execution_requests set state='reporting'"
            " where execution_id=? and worker_id=? and lease_id=? and state='claimed'",
            (execution_id, worker_id, lease_id),
        )
    return updated.rowcount > 0
