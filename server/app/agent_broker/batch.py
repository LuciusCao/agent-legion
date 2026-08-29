"""Batched Agent-request lookups for the workflow worker's poll pass.

The drain loop used to run one ``has_active_request`` query per agent
candidate; with thousands of ready candidates that serialized the poll
thread on DB round-trips. One chunked IN query per pass replaces them,
walking the ``idx_agent_requests_one_active_node`` partial index. Split from
``agent_broker`` to keep both modules within their size budgets.
"""

from __future__ import annotations

from server.app.db.dialect import ConnectSource
from server.app.db.transaction import read_connection

# Keep the IN list well under driver and statement-size limits.
_CHUNK_SIZE = 500


def active_request_keys(dsn: ConnectSource, job_ids: list[str]) -> set[tuple[str, str]]:
    """Return the (job_id, node_key) pairs with an active request."""
    keys: set[tuple[str, str]] = set()
    if not job_ids:
        return keys
    with read_connection(dsn) as conn:
        for start in range(0, len(job_ids), _CHUNK_SIZE):
            chunk = job_ids[start : start + _CHUNK_SIZE]
            placeholders = ", ".join("%s" for _ in chunk)
            rows = conn.execute(
                "select job_id, node_key from agent_execution_requests"
                f" where job_id in ({placeholders})"
                " and state in ('queued', 'claimed', 'reporting')",
                chunk,
            ).fetchall()
            keys.update((str(row["job_id"]), str(row["node_key"])) for row in rows)
    return keys
