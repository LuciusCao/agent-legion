"""Schema v92 (#933, #440 P2): agent request profile source columns.

Pins the migration side: fresh and upgraded databases land
``profile_source`` (NOT NULL, default ``agent_definition``, CHECK-bounded),
``runtime`` and ``requires_labels_json`` (nullable) under the v92 name; an
existing queued row upgrades to the legacy source; replay is idempotent.
"""

from __future__ import annotations

import pytest
from psycopg import errors

from server.app.db.schema import SCHEMA_VERSION, init_db
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL

_COLUMNS = ("profile_source", "runtime", "requires_labels_json")


def _columns(conn) -> dict[str, dict]:
    rows = conn.execute(
        "select column_name, is_nullable, data_type, column_default"
        " from information_schema.columns where table_schema=current_schema()"
        " and table_name='agent_execution_requests' and column_name = any(%s)",
        (list(_COLUMNS),),
    ).fetchall()
    return {str(row["column_name"]): dict(row) for row in rows}


def _seed_row(conn, execution_id: str, **columns: str) -> None:
    conn.execute(
        "insert into workspaces(id, name) values ('ps-ws', 'PS') on conflict(id) do nothing"
    )
    conn.execute(
        "insert into jobs(id, workspace_id, source_type, source_id)"
        " values (%s, 'ps-ws', 'question', %s) on conflict(id) do nothing",
        (execution_id, execution_id),
    )
    names = ", ".join(columns)
    values = "".join(", %s" for _ in columns)
    conn.execute(
        "insert into agent_execution_requests(execution_id, workspace_id, job_id, node_key,"
        " agent_id, agent_definition_hash, node_concurrency_limit, queued_at, manifest_json"
        + (", " + names if columns else "")
        + ") values (%s, 'ps-ws', %s, 'n', 'a', 'h', 1, current_timestamp, '{}'"
        + values
        + ")",
        (execution_id, execution_id, *columns.values()),
    )


def test_fresh_schema_has_the_profile_source_columns() -> None:
    assert SCHEMA_VERSION >= 92
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = _columns(conn)
    assert set(columns) == set(_COLUMNS)
    assert columns["profile_source"]["is_nullable"] == "NO"
    assert "agent_definition" in str(columns["profile_source"]["column_default"])
    assert columns["runtime"]["is_nullable"] == "YES"
    assert columns["requires_labels_json"]["is_nullable"] == "YES"
    assert {column["data_type"] for column in columns.values()} == {"text"}


def test_profile_source_is_check_bounded() -> None:
    with pytest.raises(errors.CheckViolation), write_transaction(TEST_DATABASE_URL) as conn:
        _seed_row(conn, "bad-source", profile_source="definition")


@pytest.mark.fresh_schema
def test_upgrade_from_v90_backfills_existing_rows_as_legacy() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from schema_migrations where version > 90")
        for column in _COLUMNS:
            conn.execute(f"alter table agent_execution_requests drop column if exists {column}")
        _seed_row(conn, "pre-v92")

    init_db(TEST_DATABASE_URL)
    init_db(TEST_DATABASE_URL)  # replay is a no-op

    with read_connection(TEST_DATABASE_URL) as conn:
        columns = _columns(conn)
        migration = conn.execute("select name from schema_migrations where version=92").fetchone()
        row = conn.execute(
            "select profile_source, runtime, requires_labels_json"
            " from agent_execution_requests where execution_id='pre-v92'"
        ).fetchone()
    assert set(columns) == set(_COLUMNS)
    assert migration is not None and migration["name"] == "agent_request_profile_source"
    assert dict(row) == {
        "profile_source": "agent_definition",
        "runtime": None,
        "requires_labels_json": None,
    }
