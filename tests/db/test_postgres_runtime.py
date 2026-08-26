from __future__ import annotations

import os
from pathlib import Path

import pytest

import server.app.db.pools as pools_module
from server.app.db.connection import connect_database
from server.app.db.transaction import read_connection, write_transaction
from tests.helpers.postgres_schema import assert_schema_initialization_is_idempotent
from tests.postgres_support import TEST_DATABASE_URL


def test_runtime_rejects_sqlite_urls() -> None:
    with pytest.raises(ValueError, match="PostgreSQL URL"):
        connect_database("data/agent_legion.sqlite")


def test_schema_initialization_is_idempotent() -> None:
    assert_schema_initialization_is_idempotent()


def test_write_transaction_rolls_back() -> None:
    with (
        pytest.raises(RuntimeError, match="rollback"),
        write_transaction(TEST_DATABASE_URL) as conn,
    ):
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values (%s, %s, 'demo_workflow')",
            ("rolled-back", "Rolled back"),
        )
        raise RuntimeError("rollback")
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute("select 1 from workspaces where id=%s", ("rolled-back",)).fetchone()
    assert row is None


def test_connection_pool_reuses_short_lived_connections(tmp_path: Path) -> None:
    del tmp_path
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values (%s, %s, 'demo_workflow')",
            ("pool", "Pool"),
        )
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute("select name from workspaces where id=%s", ("pool",)).fetchone()
    assert row == {"name": "Pool"}


def test_failed_checkout_does_not_tear_down_pools(monkeypatch: pytest.MonkeyPatch) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("select 1")
    pool = pools_module._POOLS[(os.getpid(), TEST_DATABASE_URL)]

    def boom() -> None:
        raise RuntimeError("checkout failed")

    monkeypatch.setattr(pool, "getconn", boom)
    with pytest.raises(RuntimeError, match="checkout failed"):
        connect_database(TEST_DATABASE_URL)
    monkeypatch.undo()

    assert pools_module._POOLS[(os.getpid(), TEST_DATABASE_URL)] is pool
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute("select 1 as ok").fetchone()
    assert row == {"ok": 1}


def test_workspace_default_workflow_key_has_no_column_default() -> None:
    """The platform ships no default workflow: the column is NOT NULL without
    a default, so every workspace names its workflow explicitly."""
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select column_default from information_schema.columns"
            " where table_schema=current_schema() and table_name='workspaces'"
            " and column_name='default_workflow_key'"
        ).fetchone()
    assert row is not None
    assert row["column_default"] is None
