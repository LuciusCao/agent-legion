from __future__ import annotations

import os
from pathlib import Path

import pytest

import server.app.db.connection as connection_module
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
    assert {
        "jobs",
        "executor_leases",
        "node_shards",
        "agent_definitions",
        "agent_workers",
        "agent_execution_requests",
        "workspace_node_routes",
        "workspace_node_capacities",
        "workspace_agent_capacities",
    } <= names
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = {
            row["column_name"]
            for row in conn.execute(
                "select column_name from information_schema.columns"
                " where table_schema=current_schema() and table_name='agent_workers'"
            ).fetchall()
        }
    assert {"capabilities_json", "models_json"} <= columns


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


def test_failed_checkout_does_not_tear_down_pools(monkeypatch: pytest.MonkeyPatch) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("select 1")
    pool = connection_module._POOLS[(os.getpid(), TEST_DATABASE_URL)]

    def boom() -> None:
        raise RuntimeError("checkout failed")

    monkeypatch.setattr(pool, "getconn", boom)
    with pytest.raises(RuntimeError, match="checkout failed"):
        connect_database(TEST_DATABASE_URL)
    monkeypatch.undo()

    assert connection_module._POOLS[(os.getpid(), TEST_DATABASE_URL)] is pool
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute("select 1 as ok").fetchone()
    assert row == {"ok": 1}


def test_workspace_default_workflow_key_matches_code_default() -> None:
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select column_default from information_schema.columns"
            " where table_schema=current_schema() and table_name='workspaces'"
            " and column_name='default_workflow_key'"
        ).fetchone()
    assert row is not None
    assert "question_comprehension_info" in str(row["column_default"])
