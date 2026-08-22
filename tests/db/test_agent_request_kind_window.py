"""Schema v51: idx_agent_requests_queued_head gains kind (issue #125).

The claim scan windows per kind; the queued-head partial index must serve
the (workspace_id, kind)-filtered per-kind window scans.
"""

from __future__ import annotations

import pytest

from server.app.db.migrations import migrate_agent_request_kind_window
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def _indexdef(conn) -> str:
    row = conn.execute(
        "select indexdef from pg_indexes"
        " where schemaname=current_schema() and indexname='idx_agent_requests_queued_head'"
    ).fetchone()
    return str(row["indexdef"]) if row is not None else ""


def test_queued_head_index_carries_kind() -> None:
    # The autouse fixture already ran init_db at the current SCHEMA_VERSION.
    with read_connection(TEST_DATABASE_URL) as conn:
        indexdef = _indexdef(conn)
    assert "(workspace_id, kind, queued_at, execution_id)" in indexdef
    assert "state = 'queued'::text" in indexdef


@pytest.mark.fresh_schema
def test_migration_rebuilds_v50_index_shape() -> None:
    """A database stuck at the v50 index shape gets the keyed-by-kind rebuild."""
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("drop index if exists idx_agent_requests_queued_head")
        conn.execute(
            "create index idx_agent_requests_queued_head"
            " on agent_execution_requests(workspace_id, queued_at, execution_id)"
            " where state = 'queued'"
        )
        migrate_agent_request_kind_window(conn)
        assert "(workspace_id, kind, queued_at, execution_id)" in _indexdef(conn)
        # Idempotent on replay.
        migrate_agent_request_kind_window(conn)
        assert "(workspace_id, kind, queued_at, execution_id)" in _indexdef(conn)
