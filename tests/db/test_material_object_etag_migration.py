"""Schema v98: ``materials.object_etag``（#1279 材料所有权/ETag 协议）。

Pins the migration side: fresh installs and upgrades from v97 (main's
``material_upload_generation`` shape) both land the non-null text column
(default ''), and the chain tail carries the v98 name. Mirrors
test_material_upload_generation_migration.py (v97).
"""

from __future__ import annotations

import pytest

from server.app.db.schema import SCHEMA_VERSION, init_db
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def _object_etag_column(conn) -> dict | None:
    return conn.execute(
        "select is_nullable, data_type, column_default from information_schema.columns"
        " where table_schema=current_schema() and table_name='materials'"
        " and column_name='object_etag'"
    ).fetchone()


def test_fresh_schema_has_object_etag_column() -> None:
    # The autouse fixture already ran init_db at SCHEMA_VERSION.
    assert SCHEMA_VERSION >= 98
    with read_connection(TEST_DATABASE_URL) as conn:
        column = _object_etag_column(conn)
    assert column is not None
    assert column["data_type"] == "text"
    # 非空 + '' 默认：存量行是过渡态（启动回填收口；消费侧 etag='' 在
    # 回填完成前维持 size-only 现状）。
    assert column["is_nullable"] == "NO"
    assert column["column_default"] == "''::text"


@pytest.mark.fresh_schema
def test_upgrade_from_v97_adds_the_column() -> None:
    # A database recorded at v97 replays the schema file (no-op) and runs the
    # v98 migration: the guarded ALTER is the only widening path.
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from schema_migrations where version >= 98")
        conn.execute("alter table materials drop column if exists object_etag")
    init_db(TEST_DATABASE_URL)
    with read_connection(TEST_DATABASE_URL) as conn:
        column = _object_etag_column(conn)
        row = conn.execute("select name from schema_migrations where version=%s", (98,)).fetchone()
    assert row is not None and row["name"] == "material_object_etag"
    assert column is not None and column["is_nullable"] == "NO"
