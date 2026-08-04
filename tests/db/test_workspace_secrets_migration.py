"""Schema v16: workspace_secrets vault table."""

from __future__ import annotations

from server.app.db.schema import SCHEMA_VERSION, migrate_workspace_secrets
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def test_workspace_secrets_table_exists() -> None:
    # The autouse fixture already ran init_db at the current SCHEMA_VERSION.
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = {
            row["column_name"]
            for row in conn.execute(
                "select column_name from information_schema.columns"
                " where table_schema=current_schema() and table_name='workspace_secrets'"
            ).fetchall()
        }
    assert columns == {"workspace_id", "name", "ciphertext", "created_at", "updated_at"}


def test_schema_v21_recorded() -> None:
    assert SCHEMA_VERSION == 21
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select name from schema_migrations where version=?", (SCHEMA_VERSION,)
        ).fetchone()
    assert row is not None
    assert row["name"] == "global_settings"


def test_agent_requests_done_recent_index_exists() -> None:
    """Schema v20: partial index backing the stockpile done-rate window scan."""
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select indexname from pg_indexes"
            " where schemaname=current_schema()"
            " and indexname='idx_agent_requests_done_recent'"
        ).fetchone()
    assert row is not None


def test_migrate_workspace_secrets_is_idempotent() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        migrate_workspace_secrets(conn)
        conn.execute(
            "insert into workspaces(id, name) values ('idem-ws', 'idem-ws') on conflict do nothing"
        )
        conn.execute(
            "insert into workspace_secrets(workspace_id, name, ciphertext)"
            " values ('idem-ws', 'token', 'cipher') on conflict do nothing"
        )
        migrate_workspace_secrets(conn)
        row = conn.execute(
            "select ciphertext from workspace_secrets where workspace_id='idem-ws' and name='token'"
        ).fetchone()
    assert row["ciphertext"] == "cipher"
