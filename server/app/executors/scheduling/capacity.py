"""Cheap per-pass code-pool capacity snapshot for the workflow scheduler.

Single implicit code pool (P-0.5): the snapshot is built once per poll pass
from two aggregate queries (active lease counts + configured node limits)
and is an optimization hint only — the lease claim transaction remains the
authoritative capacity enforcement. Local counters are decremented after
each successful claim so one pass can claim multiple nodes without
re-querying.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from server.app.db.transaction import read_connection
from server.app.executors._lease_transactions import database_timestamp
from server.app.executors.models import CODE_EXECUTOR_ID


@dataclass
class CapacitySnapshot:
    """Remaining claim capacity: one global number plus per-node remainders."""

    global_remaining: int = 0
    node_remaining: dict[tuple[str, str, str], int] = field(default_factory=dict)

    def has_any_capacity(self) -> bool:
        """Return True when the pool still has global claim capacity."""
        return self.global_remaining > 0

    def has_capacity(self, workspace_id: str, workflow_key: str, node_key: str) -> bool:
        """Return True when a claim for this node looks worthwhile.

        Nodes without a configured limit have no per-node ceiling; the global
        count and the claim transaction still apply.
        """
        if self.global_remaining <= 0:
            return False
        remaining = self.node_remaining.get((workspace_id, workflow_key, node_key))
        return remaining is None or remaining > 0

    def record_claim(self, workspace_id: str, workflow_key: str, node_key: str) -> None:
        """Decrement local counters after a successful claim."""
        self.global_remaining = max(self.global_remaining - 1, 0)
        key = (workspace_id, workflow_key, node_key)
        if key in self.node_remaining:
            self.node_remaining[key] = max(self.node_remaining[key] - 1, 0)


def load_capacity_snapshot(db_path: str, code_capacity: int) -> CapacitySnapshot:
    """Build the snapshot from lease aggregates and the configured node limits.

    The global count covers only local code-pool leases (Worker-claimed
    executions are capacity-accounted on the Worker side); node-level counts
    cover every active lease of the node, matching the claim transaction.
    """
    with read_connection(db_path) as conn:
        now_str = database_timestamp(datetime.now(UTC))
        global_row = conn.execute(
            """
            select count(*) as cnt
            from executor_leases
            where executor_id=%s and status='active' and expires_at>%s
            """,
            (CODE_EXECUTOR_ID, now_str),
        ).fetchone()
        node_rows = conn.execute(
            """
            select workspace_id, workflow_key, node_key, count(*) as cnt
            from executor_leases
            where status='active' and expires_at>%s
            group by workspace_id, workflow_key, node_key
            """,
            (now_str,),
        ).fetchall()
        limit_rows = conn.execute(
            """
            select workspace_id, workflow_key, node_key, concurrency_limit
            from workspace_node_limits
            """
        ).fetchall()

    used_global = int(global_row["cnt"]) if global_row is not None else 0
    used_nodes = {
        (str(row["workspace_id"]), str(row["workflow_key"]), str(row["node_key"])): int(row["cnt"])
        for row in node_rows
    }
    snapshot = CapacitySnapshot(global_remaining=max(code_capacity - used_global, 0))
    for row in limit_rows:
        key = (str(row["workspace_id"]), str(row["workflow_key"]), str(row["node_key"]))
        snapshot.node_remaining[key] = max(
            int(row["concurrency_limit"]) - used_nodes.get(key, 0), 0
        )
    return snapshot
