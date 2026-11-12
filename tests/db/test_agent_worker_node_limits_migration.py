"""Schema v94: ``agent_workers.node_concurrency_limits_json``（#1158 Worker 节点级并发上限）。

Pins the migration side: fresh installs and upgrades from v93 both land the
non-null text column (default '{}'), and the chain tail carries the v94 name.
Mirrors test_agent_worker_claim_state_migration.py (v87).
"""

from __future__ import annotations

import pytest

from server.app.db.schema import SCHEMA_VERSION, init_db
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def _node_limits_column(conn) -> dict | None:
    return conn.execute(
        "select is_nullable, data_type, column_default from information_schema.columns"
        " where table_schema=current_schema() and table_name='agent_workers'"
        " and column_name='node_concurrency_limits_json'"
    ).fetchone()


def test_fresh_schema_has_node_concurrency_limits_column() -> None:
    # The autouse fixture already ran init_db at SCHEMA_VERSION.
    assert SCHEMA_VERSION >= 94
    with read_connection(TEST_DATABASE_URL) as conn:
        column = _node_limits_column(conn)
    assert column is not None
    assert column["data_type"] == "text"
    # 非空 + '{}' 默认：未声明（旧 Worker）与显式空 map 同义 = 无限制。
    assert column["is_nullable"] == "NO"
    assert column["column_default"] == "'{}'::text"


@pytest.mark.fresh_schema
def test_upgrade_from_v93_adds_the_column() -> None:
    # A database recorded at v93 replays the schema file (no-op) and runs the
    # v94 migration: the guarded ALTER is the only widening path.
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from schema_migrations where version >= 94")
        conn.execute("alter table agent_workers drop column if exists node_concurrency_limits_json")
    init_db(TEST_DATABASE_URL)
    with read_connection(TEST_DATABASE_URL) as conn:
        column = _node_limits_column(conn)
        row = conn.execute("select name from schema_migrations where version=%s", (94,)).fetchone()
    assert row is not None and row["name"] == "agent_worker_node_limits"
    assert column is not None and column["is_nullable"] == "NO"
