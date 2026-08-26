"""workflow_node_codes custom node code table (v25 era, registry-retired DDL)."""

from __future__ import annotations

import pytest
from psycopg import IntegrityError

from server.app.db.migrations import migrate_custom_node_codes
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def _seed_workspace(conn, workspace_id: str) -> None:
    conn.execute(
        "insert into workspaces(id, name, default_workflow_key) values (%s, %s, 'question_comprehension_info') on conflict do nothing",
        (workspace_id, workspace_id),
    )


def _insert_version(conn, workspace_id: str, version: int, status: str) -> None:
    conn.execute(
        "insert into workflow_node_codes("
        "id, workspace_id, workflow_key, node_key, version, status, code, code_hash,"
        " created_by)"
        " values (%s, %s, 'question_comprehension_info', 'fetch_questions', %s, %s,"
        " 'def run(job, job_dir, runtime):\n    pass\n', 'hash', 'user:test')",
        (f"{workspace_id}-v{version}", workspace_id, version, status),
    )


def test_workflow_node_codes_table_exists() -> None:
    # The autouse fixture already ran init_db at the current SCHEMA_VERSION.
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = {
            row["column_name"]
            for row in conn.execute(
                "select column_name from information_schema.columns"
                " where table_schema=current_schema() and table_name='workflow_node_codes'"
            ).fetchall()
        }
    assert columns == {
        "id",
        "workspace_id",
        "workflow_key",
        "node_key",
        "version",
        "status",
        "code",
        "code_hash",
        "created_by",
        "change_note",
        "created_at",
        "published_at",
    }


def test_published_partial_unique_index() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "cnc-uniq-ws")
        _insert_version(conn, "cnc-uniq-ws", 1, "published")
        # Drafts and archived rows coexist with the published one.
        _insert_version(conn, "cnc-uniq-ws", 2, "draft")
        _insert_version(conn, "cnc-uniq-ws", 3, "archived")
    # A second published row for the same node violates the partial index.
    with pytest.raises(IntegrityError), write_transaction(TEST_DATABASE_URL) as conn:
        _insert_version(conn, "cnc-uniq-ws", 4, "published")


def test_migration_is_idempotent() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        migrate_custom_node_codes(conn)
        _seed_workspace(conn, "cnc-idem-ws")
        _insert_version(conn, "cnc-idem-ws", 1, "published")
        migrate_custom_node_codes(conn)
        row = conn.execute(
            "select status from workflow_node_codes where id='cnc-idem-ws-v1'"
        ).fetchone()
    assert row["status"] == "published"
