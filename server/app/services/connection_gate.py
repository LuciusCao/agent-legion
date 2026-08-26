"""Connection-scoped advisory gate shared by token refresh and admin updates.

``ConnectionTokenService._refresh_token`` re-reads config/enabled and writes
the exchanged token while holding this transaction-scoped lock;
``ConnectionService.update``/``delete`` invalidate cached tokens and rewrite
credentials under the same lock. Sharing it means the two can never interleave:
a refresh that already resolved the old config cannot write its token past a
concurrent admin update (the update queues on the gate and deletes the token
after the refresh commits), and an update cannot yank credentials mid-exchange
— the refresh re-checks under the lock it already holds. The lock keys on the
connection key text, so unrelated connections never queue on each other.
"""

from __future__ import annotations

from typing import Any

_GATE_PREFIX = "agent-legion-conn-token:"


def lock_connection_gate(conn: Any, key: str) -> None:
    """Take the connection's token/config gate (pg_advisory_xact_lock)."""
    conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (_GATE_PREFIX + key,))
