from __future__ import annotations

from typing import Any

from server.app.db.transaction import write_transaction
from server.app.executors._lease_approval import park_awaiting_approval


def park_awaiting_approval_repo(
    repo: Any,
    job_id: str,
    node_key: str,
    *,
    execution_generation: int = 0,
    control_snapshot: dict[str, Any] | None = None,
    allowed_node_keys: frozenset[str] | None = None,
) -> bool:
    """Repository write path: one transaction per park (leases.py delegate)."""
    with write_transaction(repo.path) as conn:
        return park_awaiting_approval(
            conn,
            job_id,
            node_key,
            execution_generation=execution_generation,
            control_snapshot=control_snapshot,
            allowed_node_keys=allowed_node_keys,
        )
