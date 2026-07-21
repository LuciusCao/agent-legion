from __future__ import annotations

from pathlib import Path

import pytest

from server.app.db.connection import connect_database
from server.app.db.schema import init_db
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def test_runtime_rejects_sqlite_urls() -> None:
    with pytest.raises(ValueError, match="PostgreSQL URL"):
        connect_database("data/video_hive.sqlite")


def test_schema_initialization_is_idempotent() -> None:
    init_db(TEST_DATABASE_URL)
    init_db(TEST_DATABASE_URL)
    with read_connection(TEST_DATABASE_URL) as conn:
        rows = conn.execute(
            "select table_name from information_schema.tables where table_schema=current_schema()"
        ).fetchall()
    names = {str(row["table_name"]) for row in rows}
    assert {"jobs", "executor_leases", "remote_executions", "node_shards"} <= names


def test_write_transaction_rolls_back() -> None:
    with (
        pytest.raises(RuntimeError, match="rollback"),
        write_transaction(TEST_DATABASE_URL) as conn,
    ):
        conn.execute(
            "insert into workspaces(id, name) values (?, ?)",
            ("rolled-back", "Rolled back"),
        )
        raise RuntimeError("rollback")
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute("select 1 from workspaces where id=?", ("rolled-back",)).fetchone()
    assert row is None


def test_connection_pool_reuses_short_lived_connections(tmp_path: Path) -> None:
    del tmp_path
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("insert into workspaces(id, name) values (?, ?)", ("pool", "Pool"))
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute("select name from workspaces where id=?", ("pool",)).fetchone()
    assert row == {"name": "Pool"}
