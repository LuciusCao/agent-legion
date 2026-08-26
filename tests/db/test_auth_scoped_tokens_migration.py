"""Schema v41/v42: auth_scoped_tokens scoped bearer token table."""

from __future__ import annotations

import pytest

from server.app.db.schema import SCHEMA_VERSION, init_db
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
        "workspace_id",
        "expires_at",
        "revoked_at",
        "created_at",
    }


@pytest.mark.fresh_schema
def test_v41_database_upgrades_via_init_db() -> None:
    # Reproduce the real upgrade path: a database that applied v41 has an
    # auth_scoped_tokens table without id/origin and no current
    # schema_migrations row, so init_db replays the whole schema file —
    # create table if not exists skips the old table, and the file's own
    # alter statements must add the columns before the unique index on id
    # can be built.
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from schema_migrations where version=%s", (SCHEMA_VERSION,))
        conn.execute("alter table auth_scoped_tokens drop column origin")
        # Dropping the column also drops the idx_auth_scoped_tokens_id index.
        conn.execute("alter table auth_scoped_tokens drop column id")
        # Pre-v45 databases also lack the workspace binding column.
        conn.execute("alter table auth_scoped_tokens drop column workspace_id")
        conn.execute("insert into users(id, username) values ('u-legacy', 'legacy-user')")
        conn.execute(
            "insert into auth_scoped_tokens(token_hash, user_id, scope, expires_at)"
            " values ('legacy-hash', 'u-legacy', 'studio_agent', current_timestamp)"
        )

    init_db(TEST_DATABASE_URL)

    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select id, origin, workspace_id from auth_scoped_tokens where token_hash='legacy-hash'"
        ).fetchone()
        indexes = {
            r["indexname"]
            for r in conn.execute(
                "select indexname from pg_indexes"
                " where schemaname=current_schema() and tablename='auth_scoped_tokens'"
            ).fetchall()
        }
        migration = conn.execute(
            "select name from schema_migrations where version=%s", (SCHEMA_VERSION,)
        ).fetchone()
    # Existing rows survive with origin='run', a backfilled public id, and no
    # workspace binding (NULL = unbound legacy/self-service token).
    assert row is not None
    assert row["origin"] == "run"
    assert row["id"]
    assert row["workspace_id"] is None
    assert "idx_auth_scoped_tokens_id" in indexes
    assert migration is not None
    assert migration["name"] == "retire_global_register_tokens"

    # Idempotent on replay (init_db runs at every backend startup).
    init_db(TEST_DATABASE_URL)
