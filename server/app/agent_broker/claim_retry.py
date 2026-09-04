"""Claim transaction + deadlock retry (issue #437, split from ``broker.py``
for the file-size budget).

The claim transaction's jobs promote UPDATE can lose a lock race on the
run/workspace status-counter rows and come back as SQLSTATE 40P01. One
immediate retry absorbs the blip without burning a Worker poll cycle; a
second failure propagates to the route's 500 (the Worker's own backoff
loop then takes over).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

from psycopg import Error

from server.app.agent_broker.claim import claim_in_transaction
from server.app.agent_broker.claim_scan import AgentClaim
from server.app.db.transaction import write_transaction

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker

T = TypeVar("T")

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


def run_claim_with_deadlock_retry(operation: Callable[[], T]) -> T:
    """Generic single-retry wrapper for the 40P01 sqlstate (test surface).

    The production path is ``claim_with_retry``; this helper exists so tests
    can pin the retry contract (one retry, sqlstate-matched, never type-
    matched) without a database.
    """
    for attempt in range(1 + _CLAIM_DEADLOCK_RETRIES):
        try:
            return operation()
        except Error as exc:
            if getattr(exc, "sqlstate", None) != "40P01" or attempt >= _CLAIM_DEADLOCK_RETRIES:
                raise
    raise RuntimeError("unreachable: retry loop exhausted")
