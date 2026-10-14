"""Schema v99: ``materials.legacy_etag_bound_at``（#1316 存量行 ETag 绑定时刻）。

Pins the migration side: fresh installs and upgrades from v98 both land the
nullable timestamptz column (existing rows stay NULL = not binder-bound), and
the chain tail carries the v99 name. Mirrors test_material_object_etag_migration.py.
"""

from __future__ import annotations

import pytest

from server.app.db.schema import SCHEMA_VERSION, init_db
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def _column(conn) -> dict | None:
    return conn.execute(
        "select is_nullable, data_type, column_default from information_schema.columns"
        " where table_schema=current_schema() and table_name='materials'"
        " and column_name='legacy_etag_bound_at'"
    ).fetchone()


def test_fresh_schema_has_legacy_etag_bound_at_column() -> None:
    assert SCHEMA_VERSION >= 99
    with read_connection(TEST_DATABASE_URL) as conn:
        column = _column(conn)
    assert column is not None
    assert column["data_type"] == "timestamp with time zone"
    # 可空、无默认：存量行与新 complete 行都不是 binder 绑定的行。
    assert column["is_nullable"] == "YES"
    assert column["column_default"] is None


@pytest.mark.fresh_schema
def test_upgrade_from_v98_adds_the_column() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from schema_migrations where version >= 99")
        conn.execute("alter table materials drop column if exists legacy_etag_bound_at")
    init_db(TEST_DATABASE_URL)
    with read_connection(TEST_DATABASE_URL) as conn:
        column = _column(conn)
        row = conn.execute("select name from schema_migrations where version=%s", (99,)).fetchone()
    assert row is not None and row["name"] == "material_legacy_etag_binding"
    assert column is not None and column["is_nullable"] == "YES"
