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
    # The pin narrative now lives in tests/db/test_workspace_id_key_binding.py;
    # v76 (studio_publish_requests, #416) is the current chain tail.
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select name from schema_migrations where version=%s", (SCHEMA_VERSION,)
        ).fetchone()
    assert row is not None
    assert row["name"] == "studio_publish_requests"


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
        "session_modes_json",
        "config_options_json",
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
        assert migration["name"] == "studio_publish_requests"


@pytest.mark.fresh_schema
def test_v42_database_upgrades_via_init_db() -> None:
    # Reproduce the real upgrade path: a database that applied v42 has no
    # studio chat tables and no current schema_migrations row, so init_db
    # replays the whole schema file — the file's own create-if-not-exists
    # statements must create the tables (the migrations module replays the
    # same DDL as the idempotent fallback).
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from schema_migrations where version=%s", (SCHEMA_VERSION,))
        # v76's studio_publish_requests references studio_chat_sessions; drop
        # it before the sessions table (the schema replay recreates all three).
        conn.execute("drop table if exists studio_publish_requests")
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
        assert migration["name"] == "studio_publish_requests"

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


@pytest.mark.fresh_schema
def test_v73_database_gains_agent_config_columns_via_init_db() -> None:
    # Pre-v74 databases lack the agent config mirrors (#368); init_db replays
    # the schema file whose ALTERs add both columns.
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from schema_migrations where version=%s", (SCHEMA_VERSION,))
        conn.execute("alter table studio_chat_sessions drop column session_modes_json")
        conn.execute("alter table studio_chat_sessions drop column config_options_json")

    init_db(TEST_DATABASE_URL)

    with read_connection(TEST_DATABASE_URL) as conn:
        columns = _columns(conn, "studio_chat_sessions")
        assert "session_modes_json" in columns
        assert "config_options_json" in columns
