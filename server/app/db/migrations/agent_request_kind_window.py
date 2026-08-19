"""Queued-head index rebuild for per-kind claim windows (schema v51, issue #125).

The claim scan is per kind since v51 (``agent_broker.claim_windows``): each
kind walks its own ``(workspace_id, kind)``-filtered queued window, so the
queued-head partial index gains ``kind`` as its second column. Plain
drop/create inside the migration transaction — the partial index covers
queued rows only, and CREATE INDEX CONCURRENTLY cannot run inside the
transaction ``init_db`` migrates under. Idempotent on replay.
"""

from __future__ import annotations

from typing import Any


def migrate_agent_request_kind_window(conn: Any) -> None:
    """Rebuild idx_agent_requests_queued_head with kind in the key (v51)."""
    conn.execute("drop index if exists idx_agent_requests_queued_head")
    conn.execute(
        "create index if not exists idx_agent_requests_queued_head"
        " on agent_execution_requests(workspace_id, kind, queued_at, execution_id)"
        " where state = 'queued'"
    )
