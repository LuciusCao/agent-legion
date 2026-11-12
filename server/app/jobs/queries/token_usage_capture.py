"""Token-usage capture seams for finished leases (#1239).

Both checkouts of the lease-finish token capture live here on the facade so
the service layer (``services/token_usage_lease.py``) orchestrates read →
events.jsonl parse → persist without touching ``db.transaction`` or a raw
DSN (BOUNDARY-DATA-001). The two methods are deliberately separate: the
caller runs them as SEQUENTIAL pool checkouts — never nested — with the
pure-filesystem parse in between, so a size-1 pool
(``AGENT_LEGION_DB_POOL_MAX_SIZE=1``) can serve the whole capture.
"""

from __future__ import annotations

from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin
from server.app.services.token_usage import persist_node_run_usage
from server.app.services.token_usage_parse import TokenUsageSummary


class TokenUsageCaptureQueriesMixin(ConnectionQueriesMixin):
    """Read/persist seams for lease-finish token-usage capture."""

    def read_lease_token_usage_source(self, lease_id: str) -> dict[str, Any] | None:
        """lease + node_run rows the events.jsonl parse needs (own read checkout).

        Returns ``{"workspace_id": ..., "node_run": {...}}``, or None when the
        lease has no node run / the run has no run_dir (nothing to parse).
        """
        with self._connect_read() as conn:
            lease = conn.execute(
                "select node_run_id, workspace_id from executor_leases where id=%s", (lease_id,)
            ).fetchone()
            if lease is None or not lease["node_run_id"]:
                return None
            node_run = conn.execute(
                "select * from node_runs where id=%s", (lease["node_run_id"],)
            ).fetchone()
            if node_run is None or not node_run["run_dir"]:
                return None
        return {"workspace_id": lease["workspace_id"], "node_run": dict(node_run)}

    def persist_lease_token_usage(self, summary: TokenUsageSummary) -> None:
        """Upsert the parsed usage row in its own write transaction (#1239).

        Call only after the read checkout from ``read_lease_token_usage_source``
        has been released — opening this write while a read checkout is still
        held is the nested-checkout shape this split exists to prevent.
        """
        with self.write() as conn:
            persist_node_run_usage(conn, summary)
