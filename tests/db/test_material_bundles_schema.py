"""Schema v55: material_bundles tables (materials-and-runs design §5.4, #156).

The reference-only manifest for bundle intake: a folder uploaded as ONE run
item — members stay ordinary materials, the bundle freezes material ids +
relative paths. The latest-migration record pin lives here (moved from
tests/db/test_job_artifacts_schema.py, v54).
"""

from __future__ import annotations

from server.app.db.schema import SCHEMA_VERSION
from server.app.db.transaction import read_connection
from tests.postgres_support import TEST_DATABASE_URL


def test_schema_v55_recorded() -> None:
    assert SCHEMA_VERSION == 55
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select name from schema_migrations where version=%s", (SCHEMA_VERSION,)
        ).fetchone()
    assert row is not None
    assert row["name"] == "material_bundles"


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
