"""Claim transaction + deadlock retry (issue #437, split from ``broker.py``
for the file-size budget).

The claim transaction's jobs promote UPDATE can lose a lock race on the
run/workspace status-counter rows and come back as SQLSTATE 40P01. One
immediate retry absorbs the blip without burning a Worker poll cycle; a
second failure propagates to the route's 500 (the Worker's own backoff
loop then takes over).

Why this does not reuse ``server.app.db.retry.retry_on_database_conflict``
(the two stay parallel, deliberately): the generic helper is a 5-attempt
exponential-backoff loop (0.05s doubling to 1s) matching both 40P01 and
SerializationFailure by exception TYPE — the right shape for low-frequency
background writers. The claim path is the fleet's highest-frequency call:
one immediate retry, no sleep, and a strict 40P01-only sqlstate match (a
SerializationFailure on claim is a real bug to surface, not a blip to
absorb, and the sqlstate read also covers Error subclasses the type match
would miss). Merging the two would force one of those contracts to lie.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from psycopg import Error

from server.app.agent_broker.claim import claim_in_transaction
from server.app.agent_broker.claim_scan import AgentClaim
from server.app.db.transaction import write_transaction

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker

# One retry, then the 500 stands.
_CLAIM_DEADLOCK_RETRIES = 1


def claim_with_retry(
    broker: AgentExecutionBroker,
    worker_id: str,
    declared_max_concurrency: int | None,
    declared_max_code_concurrency: int | None,
) -> tuple[AgentClaim | None, Counter[str]]:
    """Run the single claim transaction, retrying one SQLSTATE 40P01.

    ``write_transaction`` rolls the deadlocked transaction back and closes
    the connection, so the retry re-enters on a clean connection. The 40P01
    match reads ``sqlstate`` (the wire contract), not the exception type —
    a string match on the message would be locale-fragile, and isinstance
    would miss an Error subclass that carries the same sqlstate.
    ``ClaimRacedError`` is a business verdict, not a conflict: it
    propagates unchanged (the caller maps it to an empty claim).
    """
    for attempt in range(1 + _CLAIM_DEADLOCK_RETRIES):
        try:
            with write_transaction(broker.database_dsn) as conn:
                return claim_in_transaction(
                    broker,
                    conn,
                    worker_id,
                    declared_max_concurrency,
                    declared_max_code_concurrency,
                )
        except Error as exc:
            if getattr(exc, "sqlstate", None) != "40P01" or attempt >= _CLAIM_DEADLOCK_RETRIES:
                raise
    raise RuntimeError("unreachable: claim retry loop exhausted")
