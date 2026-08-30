"""Schema v43: studio chat session/message tables (ACP conversation backend)."""

from __future__ import annotations

import pytest

from server.app.db.schema import SCHEMA_VERSION, init_db
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def _columns(conn, table: str) -> set[str]:
    return {
        row["column_name"]
        for row in conn.execute(
            "select column_name from information_schema.columns"
            " where table_schema=current_schema() and table_name=%s",
            (table,),
        ).fetchall()
    }


def test_schema_v57_recorded() -> None:
    """Latest-migration record pin (moved from
    tests/db/test_job_node_status_counts_migration.py, v56)."""
    # The pin now lives in tests/db/test_retire_global_register_tokens_migration.py
    # (v58+): schema_migrations only ever records the latest version, so the
    # current SCHEMA_VERSION row must exist with the latest migration's name.
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select name from schema_migrations where version=%s", (SCHEMA_VERSION,)
        ).fetchone()
    assert row is not None
    assert row["name"] == "approval_decisions"


def test_studio_chat_tables_exist() -> None:
    # The autouse fixture already ran init_db at the current SCHEMA_VERSION.
    with read_connection(TEST_DATABASE_URL) as conn:
        session_columns = _columns(conn, "studio_chat_sessions")
        message_columns = _columns(conn, "studio_chat_messages")
    assert {
        "id",
        "workspace_id",
        "user_id",
        "agent_id",
        "title",
        "status",
        "acp_session_id",
        "capability_snapshot_json",
        "allow_all_permissions",
        "mcp_status",
        "selected_node_key",
        "draft_yaml",
        "error_detail",
        "created_at",
        "updated_at",
        "closed_at",
    } == session_columns
    assert {"id", "seq", "session_id", "kind", "role", "content_json", "created_at"} == (
        message_columns
    )


@pytest.mark.fresh_schema
def test_v56_database_gains_draft_yaml_via_init_db() -> None:
    # Pre-v57 databases have the sessions table without the draft column;
    # init_db replays the schema file whose alter statement adds it.
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from schema_migrations where version=%s", (SCHEMA_VERSION,))
        conn.execute("alter table studio_chat_sessions drop column draft_yaml")

    init_db(TEST_DATABASE_URL)

    with read_connection(TEST_DATABASE_URL) as conn:
        assert "draft_yaml" in _columns(conn, "studio_chat_sessions")
        migration = conn.execute(
            "select name from schema_migrations where version=%s", (SCHEMA_VERSION,)
        ).fetchone()
        assert migration is not None
        assert migration["name"] == "approval_decisions"


@pytest.mark.fresh_schema
def test_v42_database_upgrades_via_init_db() -> None:
    # Reproduce the real upgrade path: a database that applied v42 has no
    # studio chat tables and no current schema_migrations row, so init_db
    # replays the whole schema file — the file's own create-if-not-exists
    # statements must create the tables (the migrations module replays the
    # same DDL as the idempotent fallback).
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from schema_migrations where version=%s", (SCHEMA_VERSION,))
        conn.execute("drop table if exists studio_chat_messages")
        conn.execute("drop table if exists studio_chat_sessions")
        conn.execute("insert into users(id, username) values ('u-legacy', 'legacy-user')")
        conn.execute(
            # v62 invariant: id == key (the migration renames mismatched ids,
            # so seed rows that already satisfy it keep their ids stable).
            "insert into workspaces(id, name, default_workflow_key) values ('demo_workflow', 'legacy-ws', 'demo_workflow')"
        )

    init_db(TEST_DATABASE_URL)

    with read_connection(TEST_DATABASE_URL) as conn:
        assert "status" in _columns(conn, "studio_chat_sessions")
        assert "seq" in _columns(conn, "studio_chat_messages")
        migration = conn.execute(
            "select name from schema_migrations where version=%s", (SCHEMA_VERSION,)
        ).fetchone()
        assert migration is not None
        assert migration["name"] == "approval_decisions"

    # Rows written through the new tables survive a replay (init_db runs at
    # every backend startup).
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into studio_chat_sessions(id, workspace_id, user_id, agent_id)"
            " values ('s-1', 'demo_workflow', 'u-legacy', 'fake-agent')"
        )
        conn.execute(
            "insert into studio_chat_messages(id, session_id, kind, role)"
            " values ('m-1', 's-1', 'text', 'user')"
        )
    init_db(TEST_DATABASE_URL)
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute("select seq from studio_chat_messages where id='m-1'").fetchone()
    assert row is not None and row["seq"] >= 1
