"""Schema v84 (#626): workspace_api_tokens — the machine-to-machine intake
credential table. DDL rides the migration's apply fn (postgres_schema.sql
is at its ceiling; v76 precedent), so fresh and pre-v84 databases both run
this module and the parity test pins the shapes equal."""

from __future__ import annotations

import pytest

from server.app.db.schema import SCHEMA_VERSION, init_db
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL

_EXPECTED_COLUMNS = {
    "id",
    "token_hash",
    "workspace_id",
    "label",
    "created_at",
    "expires_at",
    "revoked_at",
    "last_used_at",
}


def test_workspace_api_tokens_table_exists() -> None:
    # The autouse fixture already ran init_db at the current SCHEMA_VERSION.
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = {
            row["column_name"]
            for row in conn.execute(
                "select column_name from information_schema.columns"
                " where table_schema=current_schema() and table_name='workspace_api_tokens'"
            ).fetchall()
        }
        indexes = {
            row["indexname"]
            for row in conn.execute(
                "select indexname from pg_indexes"
                " where schemaname=current_schema() and tablename='workspace_api_tokens'"
            ).fetchall()
        }
    assert columns == _EXPECTED_COLUMNS
    assert "idx_workspace_api_tokens_workspace" in indexes


@pytest.mark.fresh_schema
def test_v82_database_upgrades_via_init_db() -> None:
    """A v82 database (table absent) upgrades in place; rows survive."""
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("drop table workspace_api_tokens")
        # v85 (execution_generation, #759) and v86 (node_runs_impl_identity,
        # #645) trail this table's own v84, so rewinding to a pre-v84 shape
        # must drop all three rows — deleting only SCHEMA_VERSION (86) would
        # leave max(applied)=85 and the high-water skip would never re-run
        # v84's table-creating apply fn.
        conn.execute("delete from schema_migrations where version in (84, 85, 86)")
        conn.execute(
            "insert into workspaces(id, default_workflow_key, name)"
            " values ('ws-v83-upgrade', 'ws-v83-upgrade', 'upgrade witness')"
        )

    init_db(TEST_DATABASE_URL)

    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspace_api_tokens(id, token_hash, workspace_id, label)"
            " values ('tok-legacy', 'hash', 'ws-v83-upgrade', 'pre-upgrade row')"
        )
    # Idempotent on replay (init_db runs at every backend startup).
    init_db(TEST_DATABASE_URL)

    with read_connection(TEST_DATABASE_URL) as conn:
        columns = {
            row["column_name"]
            for row in conn.execute(
                "select column_name from information_schema.columns"
                " where table_schema=current_schema() and table_name='workspace_api_tokens'"
            ).fetchall()
        }
        row = conn.execute(
            "select label from workspace_api_tokens where id='tok-legacy'"
        ).fetchone()
        migration = conn.execute("select name from schema_migrations where version=84").fetchone()
        tail = conn.execute(
            "select name from schema_migrations where version=%s", (SCHEMA_VERSION,)
        ).fetchone()
    assert columns == _EXPECTED_COLUMNS
    assert row is not None and row["label"] == "pre-upgrade row"
    assert migration is not None
    assert migration["name"] == "workspace_api_tokens"
    # v84 is no longer the registry tail — the #645 v86 entry
    # (node_runs_impl_identity) is. The rows must exist after the upgrade
    # replay (one per registry entry).
    assert tail is not None
    assert tail["name"] == "node_runs_impl_identity"
