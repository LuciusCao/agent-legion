"""Schema v83: ``agent_workers.claim_enabled`` (Worker-reported claim switch).

Pins the migration side: fresh installs and upgrades from v82 both land the
nullable column, and the chain tail carries the v83 name. Mirrors
test_claim_queue_wait_profile_migration.py (v81).
"""

from __future__ import annotations

import pytest

from server.app.db.schema import SCHEMA_VERSION, init_db
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def _claim_enabled_column(conn) -> dict | None:
    return conn.execute(
        "select is_nullable, data_type from information_schema.columns"
        " where table_schema=current_schema() and table_name='agent_workers'"
        " and column_name='claim_enabled'"
    ).fetchone()


def test_fresh_schema_has_nullable_claim_enabled_column() -> None:
    # The autouse fixture already ran init_db at SCHEMA_VERSION.
    assert SCHEMA_VERSION >= 83
    with read_connection(TEST_DATABASE_URL) as conn:
        column = _claim_enabled_column(conn)
    assert column is not None
    assert column["data_type"] == "boolean"
    # NULL = never reported (pre-upgrade Worker) — must stay nullable.
    assert column["is_nullable"] == "YES"


@pytest.mark.fresh_schema
def test_upgrade_from_v82_adds_the_column() -> None:
    # A database recorded at v82 replays the schema file (no-op) and runs the
    # v83 migration: the guarded ALTER is the only widening path.
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from schema_migrations where version >= 83")
        conn.execute("alter table agent_workers drop column if exists claim_enabled")
    init_db(TEST_DATABASE_URL)
    with read_connection(TEST_DATABASE_URL) as conn:
        column = _claim_enabled_column(conn)
        row = conn.execute("select name from schema_migrations where version=%s", (83,)).fetchone()
    assert row is not None and row["name"] == "agent_worker_claim_state"
    assert column is not None and column["is_nullable"] == "YES"
