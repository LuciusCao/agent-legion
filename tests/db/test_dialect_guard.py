"""The dialect shim is gone: postgres_sql only validates (issue #17 stage 3)."""

from __future__ import annotations

import pytest

from server.app.db.dialect import postgres_sql

pytestmark = pytest.mark.no_db


def test_postgres_sql_passes_through_percent_s() -> None:
    sql = "select * from jobs where id = %s and status = %s"
    assert postgres_sql(sql) == sql


def test_postgres_sql_rejects_legacy_qmark() -> None:
    with pytest.raises(ValueError, match="legacy '\\?' placeholder"):
        postgres_sql("select * from jobs where id = ?")
