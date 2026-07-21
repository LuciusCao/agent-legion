"""Cheap per-pass executor capacity snapshot for the workflow scheduler.

The snapshot is built once per poll pass from two aggregate queries and is an
optimization hint only: the lease claim transaction remains the authoritative
capacity enforcement. Local counters are decremented after each successful
claim so one pass can claim multiple nodes without re-querying.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from server.app.db.transaction import read_connection
from server.app.executors._lease_transactions import _database_timestamp


@dataclass
class CapacitySnapshot:
    """Remaining claim capacity per executor and per (executor, workspace)."""

    global_remaining: dict[str, int] = field(default_factory=dict)
    workspace_remaining: dict[tuple[str, str], int] = field(default_factory=dict)

    def has_any_capacity(self) -> bool:
        """Return True when any executor still has global claim capacity."""
        return any(remaining > 0 for remaining in self.global_remaining.values())

    def has_capacity(self, executor_id: str, workspace_id: str) -> bool:
        """Return True when a claim for (executor, workspace) looks worthwhile.

        Unknown (executor, workspace) pairs — those without an allocation row
        — are left to the claim transaction, which surfaces configuration
        errors exactly as before.
        """
        if self.global_remaining.get(executor_id, 0) <= 0:
            return False
        remaining = self.workspace_remaining.get((executor_id, workspace_id))
        return remaining is None or remaining > 0

    def record_claim(self, executor_id: str, workspace_id: str) -> None:
        """Decrement local counters after a successful claim."""
        self.global_remaining[executor_id] = max(self.global_remaining.get(executor_id, 0) - 1, 0)
        key = (executor_id, workspace_id)
        if key in self.workspace_remaining:
            self.workspace_remaining[key] = max(self.workspace_remaining[key] - 1, 0)


def load_capacity_snapshot(db_path: str, global_capacities: dict[str, int]) -> CapacitySnapshot:
    """Build a snapshot with two aggregate queries against the lease database."""
    with read_connection(db_path) as conn:
        now_str = _database_timestamp(datetime.now(UTC))
        active_rows = conn.execute(
            """
            select executor_id, workspace_id, count(*) as cnt
            from executor_leases
            where status='active' and expires_at>?
            group by executor_id, workspace_id
            """,
            (now_str,),
        ).fetchall()
        allocation_rows = conn.execute(
            """
            select executor_id, workspace_id, concurrency_limit
            from workspace_executor_allocations
            """
        ).fetchall()

    used_global: dict[str, int] = {}
    used_workspace: dict[tuple[str, str], int] = {}
    for row in active_rows:
        executor_id = str(row["executor_id"])
        workspace_id = str(row["workspace_id"])
        used_workspace[(executor_id, workspace_id)] = int(row["cnt"])
        used_global[executor_id] = used_global.get(executor_id, 0) + int(row["cnt"])

    snapshot = CapacitySnapshot()
    for executor_id, capacity in global_capacities.items():
        snapshot.global_remaining[executor_id] = max(capacity - used_global.get(executor_id, 0), 0)
    for row in allocation_rows:
        key = (str(row["executor_id"]), str(row["workspace_id"]))
        snapshot.workspace_remaining[key] = max(
            int(row["concurrency_limit"]) - used_workspace.get(key, 0), 0
        )
    return snapshot
