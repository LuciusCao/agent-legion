"""Schema v26: versioned_entities unified table + workspace Agent defaults."""

from __future__ import annotations

import pytest
from psycopg import IntegrityError

from server.app.db.migrations import migrate_versioned_entities
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def _seed_workspace(conn, workspace_id: str) -> None:
    conn.execute(
        "insert into workspaces(id, name, default_workflow_key) values (%s, %s, 'question_comprehension_info') on conflict do nothing",
        (workspace_id, workspace_id),
    )


def _seed_node_code(conn, workspace_id: str, version: int, status: str) -> None:
    conn.execute(
        "insert into workflow_node_codes("
        "id, workspace_id, workflow_key, node_key, version, status, code, code_hash,"
        " created_by, change_note)"
        " values (%s, %s, 'question_comprehension_info', 'fetch_questions', %s, %s,"
        " 'def run(job, job_dir, runtime):\n    pass\n', 'hash-' || %s::text, 'user:test',"
        " 'note')",
        (f"{workspace_id}-v{version}", workspace_id, version, status, version),
    )


def _seed_agent(conn, agent_id: str, enabled: int = 1) -> None:
    # The legacy table was dropped by the v27 cutover; migration tests recreate
    # it to exercise the v26 copy path (fresh_schema rebuilds afterwards).
    conn.execute(
        """
        create table if not exists agent_definitions (
          agent_id text primary key,
          capability text not null,
          runtime text not null,
          definition_json text not null,
          definition_hash text not null,
          enabled integer not null default 1 check(enabled in (0, 1)),
          updated_at timestamptz not null default current_timestamp
        )
        """
    )
    conn.execute(
        "insert into agent_definitions("
        "agent_id, capability, runtime, definition_json, definition_hash, enabled)"
        " values (%s, %s, 'velites', '{\"capability\": \"' || %s || '\"}', %s, %s)"
        " on conflict(agent_id) do nothing",
        (agent_id, f"cap-{agent_id}", agent_id, f"hash-{agent_id}", enabled),
    )


def _entity_rows(conn, entity_type: str) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            "select * from versioned_entities where entity_type=%s order by entity_key, version",
            (entity_type,),
        ).fetchall()
    ]


def test_versioned_entities_table_exists() -> None:
    # The autouse fixture already ran init_db at the current SCHEMA_VERSION.
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = {
            row["column_name"]
            for row in conn.execute(
                "select column_name from information_schema.columns"
                " where table_schema=current_schema() and table_name='versioned_entities'"
            ).fetchall()
        }
    assert columns == {
        "id",
        "entity_type",
        "workspace_id",
        "entity_key",
        "version",
        "status",
        "definition_json",
        "definition_hash",
        "created_by",
        "created_at",
        "published_at",
    }


def test_workspaces_agent_default_columns_exist() -> None:
    with read_connection(TEST_DATABASE_URL) as conn:
        rows = conn.execute(
            "select column_name, column_default from information_schema.columns"
            " where table_schema=current_schema() and table_name='workspaces'"
            " and column_name like 'default_agent_%'"
        ).fetchall()
    assert {row["column_name"] for row in rows} == {
        "default_agent_provider",
        "default_agent_model",
        "default_agent_thinking",
    }


def test_published_partial_unique_index_covers_null_workspace() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into versioned_entities("
            "id, entity_type, workspace_id, entity_key, version, status,"
            " definition_json, definition_hash, created_by)"
            " values ('ve-uniq-1', 'agent', null, 'agent-uniq', 1, 'published', '{}', 'h', 'u')"
        )
        # A draft for the same global agent coexists with the published one.
        conn.execute(
            "insert into versioned_entities("
            "id, entity_type, workspace_id, entity_key, version, status,"
            " definition_json, definition_hash, created_by)"
            " values ('ve-uniq-2', 'agent', null, 'agent-uniq', 2, 'draft', '{}', 'h', 'u')"
        )
    # A second published row for the same global agent violates the index.
    with pytest.raises(IntegrityError), write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into versioned_entities("
            "id, entity_type, workspace_id, entity_key, version, status,"
            " definition_json, definition_hash, created_by)"
            " values ('ve-uniq-3', 'agent', null, 'agent-uniq', 3, 'published', '{}', 'h', 'u')"
        )


def test_version_uniqueness_covers_null_workspace() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into versioned_entities("
            "id, entity_type, workspace_id, entity_key, version, status,"
            " definition_json, definition_hash, created_by)"
            " values ('ve-ver-1', 'agent', null, 'agent-ver', 1, 'archived', '{}', 'h', 'u')"
        )
    with pytest.raises(IntegrityError), write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into versioned_entities("
            "id, entity_type, workspace_id, entity_key, version, status,"
            " definition_json, definition_hash, created_by)"
            " values ('ve-ver-2', 'agent', null, 'agent-ver', 1, 'draft', '{}', 'h', 'u')"
        )


@pytest.mark.fresh_schema
def test_migration_copies_legacy_rows_and_is_idempotent() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "ve-mig-ws")
        _seed_node_code(conn, "ve-mig-ws", 1, "archived")
        _seed_node_code(conn, "ve-mig-ws", 2, "published")
        _seed_agent(conn, "ve-mig-agent")
        _seed_agent(conn, "ve-mig-agent-disabled", enabled=0)
        migrate_versioned_entities(conn)
        node_rows = [
            row for row in _entity_rows(conn, "node_code") if row["workspace_id"] == "ve-mig-ws"
        ]
        assert [(row["version"], row["status"]) for row in node_rows] == [
            (1, "archived"),
            (2, "published"),
        ]
        assert node_rows[0]["entity_key"] == "question_comprehension_info:fetch_questions"
        assert node_rows[0]["definition_hash"] == "hash-1"
        agent_rows = [
            row
            for row in _entity_rows(conn, "agent")
            if row["entity_key"].startswith("ve-mig-agent")
        ]
        # Disabled agent definitions are not copied.
        assert [(row["entity_key"], row["status"]) for row in agent_rows] == [
            ("ve-mig-agent", "published")
        ]
        assert agent_rows[0]["workspace_id"] is None
        assert agent_rows[0]["created_by"] == "system"
        total = conn.execute("select count(*) as c from versioned_entities").fetchone()["c"]
        # Replay: no duplicates.
        migrate_versioned_entities(conn)
        total_after = conn.execute("select count(*) as c from versioned_entities").fetchone()["c"]
        # 清理重建的 legacy 表，避免污染同 worker 的后续 TRUNCATE 隔离测试。
        conn.execute("drop table if exists agent_definitions")
    assert total_after == total
