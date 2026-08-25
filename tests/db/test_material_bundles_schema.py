"""Schema v55: material_bundles tables (materials-and-runs design §5.4, #156).

The reference-only manifest for bundle intake: a folder uploaded as ONE run
item — members stay ordinary materials, the bundle freezes material ids +
relative paths.
"""

from __future__ import annotations

from server.app.db.transaction import read_connection
from tests.postgres_support import TEST_DATABASE_URL

# The latest-migration record pin (SCHEMA_VERSION + recorded name) moved to
# tests/db/test_job_node_status_counts_migration.py (v56).


def test_material_bundles_table_shape() -> None:
    # The autouse fixture already ran init_db at the current SCHEMA_VERSION.
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = {
            str(row["column_name"])
            for row in conn.execute(
                "select column_name from information_schema.columns"
                " where table_schema=current_schema() and table_name='material_bundles'"
            ).fetchall()
        }
        member_columns = {
            str(row["column_name"])
            for row in conn.execute(
                "select column_name from information_schema.columns"
                " where table_schema=current_schema()"
                " and table_name='material_bundle_members'"
            ).fetchall()
        }
    assert columns == {
        "id",
        "workspace_id",
        "name",
        "total_size_bytes",
        "file_count",
        "created_by",
        "created_at",
    }
    assert member_columns == {"bundle_id", "material_id", "path", "ordinal"}
