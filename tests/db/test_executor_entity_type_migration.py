"""Schema v30: versioned_entities entity_type CHECK widened with 'executor'."""

from __future__ import annotations

import pytest
from psycopg import IntegrityError

from server.app.db.migrations import migrate_executor_entity_type
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def _insert_executor_row(conn, entity_key: str = "code-migration-probe") -> None:
    conn.execute(
        "insert into versioned_entities("
        "id, entity_type, workspace_id, entity_key, version, status,"
        " definition_json, definition_hash, created_by)"
        " values (%s, 'executor', null, %s, 1, 'published',"
        " '{\"kind\": \"code\"}', 'hash', 'system')",
        (f"executor:{entity_key}:v1", entity_key),
    )


def test_entity_type_check_accepts_executor() -> None:
    # The autouse fixture already ran init_db at the current SCHEMA_VERSION.
    with write_transaction(TEST_DATABASE_URL) as conn:
        _insert_executor_row(conn)
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select entity_type from versioned_entities where id='executor:code-migration-probe:v1'"
        ).fetchone()
    assert row is not None
    assert row["entity_type"] == "executor"


def test_entity_type_check_still_rejects_unknown_types() -> None:
    with pytest.raises(IntegrityError), write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into versioned_entities("
            "id, entity_type, workspace_id, entity_key, version, status,"
            " definition_json, definition_hash, created_by)"
            " values ('bogus:1', 'bogus', null, 'bogus', 1, 'published',"
            " '{}', 'hash', 'system')"
        )


@pytest.mark.fresh_schema
def test_migrate_executor_entity_type_is_idempotent() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        migrate_executor_entity_type(conn)
        migrate_executor_entity_type(conn)
        _insert_executor_row(conn, "code-default-idem")
