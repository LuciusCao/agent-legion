"""Authenticate a registration generation before recording its claim switch.

Reports lock the current row and validate the presented token in the same
transaction, including unchanged reports. Registration rotation and key
deletion therefore cannot hand an in-flight write to a replacement Worker.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

from server.app.db.dialect import ConnectSource
from server.app.db.transaction import read_connection, write_transaction


def authenticated_worker_row(
    database_dsn: ConnectSource, token: str, claim_enabled: bool | None = None
) -> dict[str, Any] | None:
    """Return the authenticated row; a supplied switch is applied atomically."""
    worker_id, separator, secret = token.partition(".")
    if not separator or not worker_id or not secret:
        return None
    context = read_connection if claim_enabled is None else write_transaction
    with context(database_dsn) as conn:
        row = conn.execute(
            "select * from agent_workers where worker_id=%s"
            + (" for update" if claim_enabled is not None else ""),
            (worker_id,),
        ).fetchone()
        if row is None or row["revoked_at"] is not None:
            return None
        if not hmac.compare_digest(hashlib.sha256(secret.encode()).hexdigest(), row["token_hash"]):
            return None
        if claim_enabled is not None and row["claim_enabled"] is not claim_enabled:
            conn.execute(
                "update agent_workers set claim_enabled=%s where worker_id=%s",
                (claim_enabled, worker_id),
            )
            row = {**row, "claim_enabled": claim_enabled}
        return dict(row)
