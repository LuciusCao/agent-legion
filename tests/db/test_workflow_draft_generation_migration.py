"""Schema v95: ``workspace_workflow_draft_generation`` (#1226 五轮收口, 设计 v2).

Pins the migration side: fresh installs and upgrades from pre-v95 both land
the table, and the chain tail carries the v95 name. Mirrors
test_agent_worker_claim_state_migration.py (v87). Numbered past
origin/main's v94 (#434 collision protocol).
"""

from __future__ import annotations

import pytest

from server.app.db.schema import SCHEMA_VERSION, init_db
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def _generation_table_columns(conn) -> list[str]:
    rows = conn.execute(
        "select column_name from information_schema.columns"
        " where table_schema=current_schema()"
        " and table_name='workspace_workflow_draft_generation'"
    ).fetchall()
    return [str(row["column_name"]) for row in rows]


def test_fresh_schema_has_draft_generation_table() -> None:
    # The autouse fixture already ran init_db at SCHEMA_VERSION.
    assert SCHEMA_VERSION >= 95
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = _generation_table_columns(conn)
    assert columns == ["workspace_id", "generation"]


@pytest.mark.fresh_schema
def test_upgrade_from_pre_v95_adds_the_table() -> None:
    # A database recorded at the previous version replays the schema file
    # (no-op) and runs the v95 migration: the migration module owns the DDL.
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from schema_migrations where version >= 95")
        conn.execute("drop table if exists workspace_workflow_draft_generation")
    init_db(TEST_DATABASE_URL)
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = _generation_table_columns(conn)
        row = conn.execute("select name from schema_migrations where version=%s", (95,)).fetchone()
    assert row is not None and row["name"] == "workflow_draft_generation"
    assert columns == ["workspace_id", "generation"]
