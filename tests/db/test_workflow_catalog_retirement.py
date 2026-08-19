"""Schema v50: workflow catalog retirement (issue #112).

The global workflow key registry is dropped; workspaces keep their
``default_workflow_key`` as plain text and their own ``workflow_revisions``
rows stay the authoritative DAG.
"""

from __future__ import annotations

import logging

import pytest

from server.app.db.migrations import migrate_workflow_catalog_retirement
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def _create_catalog(conn) -> None:
    conn.execute(
        "create table if not exists workflow_catalog ("
        " key text primary key, label text not null, description text not null default '',"
        " origin text not null, definition_json text,"
        " created_at timestamptz not null default current_timestamp,"
        " updated_at timestamptz not null default current_timestamp)"
    )


def _table_exists(conn, name: str) -> bool:
    return bool(conn.execute("select to_regclass(%s) as t", (name,)).fetchone()["t"])


def test_catalog_table_absent_on_fresh_schema() -> None:
    with read_connection(TEST_DATABASE_URL) as conn:
        assert not _table_exists(conn, "workflow_catalog")


@pytest.mark.fresh_schema
def test_migration_drops_catalog_and_keeps_workspace_rows(caplog) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _create_catalog(conn)
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('ws-a', 'A', 'flow_a'), ('ws-b', 'B', 'flow_b')"
        )
        conn.execute(
            "insert into workflow_catalog(key, label, origin)"
            " values ('flow_a', 'Flow A', 'registered'),"
            "        ('flow_b', 'Flow B', 'builtin'),"
            "        ('orphan_flow', 'Orphan', 'registered')"
        )
        with caplog.at_level(logging.WARNING):
            migrate_workflow_catalog_retirement(conn)
        assert not _table_exists(conn, "workflow_catalog")
        keys = {
            row["default_workflow_key"]
            for row in conn.execute("select default_workflow_key from workspaces").fetchall()
        }
        # The workspace identifier survives the retirement untouched.
        assert keys == {"flow_a", "flow_b"}
    # Unreferenced registered keys are called out before the drop.
    assert any("orphan_flow" in record.message for record in caplog.records)


@pytest.mark.fresh_schema
def test_migration_is_idempotent_when_table_already_gone() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        assert not _table_exists(conn, "workflow_catalog")
        migrate_workflow_catalog_retirement(conn)
        assert not _table_exists(conn, "workflow_catalog")
