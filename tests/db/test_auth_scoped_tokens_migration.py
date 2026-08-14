"""Schema v41/v42: auth_scoped_tokens scoped bearer token table."""

from __future__ import annotations

import pytest

from server.app.db.migrations import migrate_scoped_token_origin
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def test_auth_scoped_tokens_table_exists() -> None:
    # The autouse fixture already ran init_db at the current SCHEMA_VERSION.
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = {
            row["column_name"]
            for row in conn.execute(
                "select column_name from information_schema.columns"
                " where table_schema=current_schema() and table_name='auth_scoped_tokens'"
            ).fetchall()
        }
    assert columns == {
        "id",
        "token_hash",
        "user_id",
        "scope",
        "origin",
        "expires_at",
        "revoked_at",
        "created_at",
    }


@pytest.mark.fresh_schema
def test_v41_to_v42_upgrade_backfills_origin_and_id() -> None:
    # Simulate a v41 table: drop the v42 columns, insert a legacy row, then
    # replay the migration. Existing rows must survive with origin='run' and
    # a backfilled public id.
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("alter table auth_scoped_tokens drop column origin")
        conn.execute("alter table auth_scoped_tokens drop column id")
        conn.execute("insert into users(id, username) values ('u-legacy', 'legacy-user')")
        conn.execute(
            "insert into auth_scoped_tokens(token_hash, user_id, scope, expires_at)"
            " values ('legacy-hash', 'u-legacy', 'studio_agent', current_timestamp)"
        )

    with write_transaction(TEST_DATABASE_URL) as conn:
        migrate_scoped_token_origin(conn)

    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select id, origin from auth_scoped_tokens where token_hash='legacy-hash'"
        ).fetchone()
        indexes = {
            r["indexname"]
            for r in conn.execute(
                "select indexname from pg_indexes"
                " where schemaname=current_schema() and tablename='auth_scoped_tokens'"
            ).fetchall()
        }
    assert row is not None
    assert row["origin"] == "run"
    assert row["id"]
    assert "idx_auth_scoped_tokens_id" in indexes

    # Idempotent on replay (init_db replays all migrations on every version bump).
    with write_transaction(TEST_DATABASE_URL) as conn:
        migrate_scoped_token_origin(conn)
