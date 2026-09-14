"""Execution-state probe for the Agent Worker data plane (#590).

The heartbeat relay beats the executor's lease snapshot; a ``not_owned``
verdict on that path is ambiguous between "lease swept/requeued" (the
Worker must react) and "execution already finished — the snapshot entry is
merely stale" (the benign completion followup that used to flood the
``execution.heartbeat_rejected`` stream). The relay probes this read-only
state per not_owned verdict — rare by construction (only the exception
path), one primary-key select, no liveness side effects.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from server.app.db.transaction import read_connection

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker


def execution_state(broker: AgentExecutionBroker, execution_id: str) -> str | None:
    """One execution's state; None = unknown id.

    'claimed'/'reporting' are the beatable states (the renewal paths'
    predicate); anything else — or no row at all — reads as settled to the
    relay's caller: nothing left to renew for that attempt."""
    with read_connection(broker.database_dsn) as conn:
        row = conn.execute(
            "select state from agent_execution_requests where execution_id=%s",
            (execution_id,),
        ).fetchone()
    return None if row is None else str(row["state"])
