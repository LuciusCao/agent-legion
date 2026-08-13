"""Schema v40: workflow_catalog table."""

from __future__ import annotations

from server.app.db.transaction import read_connection
from tests.postgres_support import TEST_DATABASE_URL


def test_workflow_catalog_table_exists() -> None:
    # The autouse fixture already ran init_db at the current SCHEMA_VERSION.
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = {
            row["column_name"]
            for row in conn.execute(
                "select column_name from information_schema.columns"
                " where table_schema=current_schema() and table_name='workflow_catalog'"
            ).fetchall()
        }
    assert columns == {
        "key",
        "label",
        "description",
        "origin",
        "definition_json",
        "created_at",
        "updated_at",
    }
