"""Schema v97: ``materials.upload_generation``（#1267 codex R3 P1 上传代次）。

Pins the migration side: fresh installs and upgrades from v96 (main's
``workflow_draft_generation`` shape) both land the non-null integer column
(default 0), and the chain tail carries the v97 name. Mirrors
test_agent_worker_node_limits_migration.py (v94); the v95→v96→v97 renumber
followed the #434 collision protocol as main took v95 (#1123) and then
v96 (#1226) first.
"""

from __future__ import annotations

import pytest

from server.app.db.schema import SCHEMA_VERSION, init_db
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def _upload_generation_column(conn) -> dict | None:
    return conn.execute(
        "select is_nullable, data_type, column_default from information_schema.columns"
        " where table_schema=current_schema() and table_name='materials'"
        " and column_name='upload_generation'"
    ).fetchone()


def test_fresh_schema_has_upload_generation_column() -> None:
    # The autouse fixture already ran init_db at SCHEMA_VERSION.
    assert SCHEMA_VERSION >= 97
    with read_connection(TEST_DATABASE_URL) as conn:
        column = _upload_generation_column(conn)
    assert column is not None
    assert column["data_type"] == "integer"
    # 非空 + 0 默认：存量行的首次 presign 重试从 0 落到 1，代次语义一致。
    assert column["is_nullable"] == "NO"
    assert column["column_default"] == "0"


@pytest.mark.fresh_schema
def test_upgrade_from_v96_adds_the_column() -> None:
    # A database recorded at v96 replays the schema file (no-op) and runs the
    # v97 migration: the guarded ALTER is the only widening path.
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from schema_migrations where version >= 97")
        conn.execute("alter table materials drop column if exists upload_generation")
    init_db(TEST_DATABASE_URL)
    with read_connection(TEST_DATABASE_URL) as conn:
        column = _upload_generation_column(conn)
        row = conn.execute("select name from schema_migrations where version=%s", (97,)).fetchone()
    assert row is not None and row["name"] == "material_upload_generation"
    assert column is not None and column["is_nullable"] == "NO"
