"""Persist the Worker-reported claim switch (``agent_workers.claim_enabled``, v83).

The report rides on the presence sync (``POST /agent-workers/self/presence``)
and every claim request implies ``true``. Both paths already authenticated
the Worker and hold its current row, so the write happens only when the
reported value differs from the stored one — an idle Worker syncing every
few seconds costs no write transactions once the state is settled.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from server.app.db.dialect import ConnectSource
from server.app.db.transaction import write_transaction


def record_claim_state(
    database_dsn: ConnectSource, worker: Mapping[str, Any], claim_enabled: bool
) -> bool:
    """Store ``claim_enabled`` for ``worker`` when it changed; returns whether it wrote."""
    if worker.get("claim_enabled") is claim_enabled:
        return False
    with write_transaction(database_dsn) as conn:
        conn.execute(
            "update agent_workers set claim_enabled=%s where worker_id=%s",
            (claim_enabled, worker["worker_id"]),
        )
    return True
