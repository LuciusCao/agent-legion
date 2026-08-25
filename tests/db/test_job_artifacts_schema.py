"""Schema v54: job_artifacts table (materials-and-runs design §6.5, D12, #160).

The manifest table for job artifacts in object storage: the authoritative
bytes live under ``jobs/{workspace_id}/{job_id}/{name}`` in the instance
bucket; the local job_dir copy is an evictable cache. The latest-migration
record pin moved to tests/db/test_material_bundles_schema.py (v55), then to
tests/db/test_job_node_status_counts_migration.py (v56).
"""

from __future__ import annotations

from server.app.db.transaction import read_connection
from tests.postgres_support import TEST_DATABASE_URL


def test_job_artifacts_table_shape() -> None:
    # The autouse fixture already ran init_db at the current SCHEMA_VERSION.
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = {
            str(row["column_name"])
            for row in conn.execute(
                "select column_name from information_schema.columns"
                " where table_schema=current_schema() and table_name='job_artifacts'"
            ).fetchall()
        }
        pk = conn.execute(
            "select conname from pg_constraint c"
            " join pg_class t on t.oid = c.conrelid"
            " join pg_namespace n on n.oid = t.relnamespace"
            " where n.nspname=current_schema() and t.relname='job_artifacts'"
            " and c.contype='p'"
        ).fetchone()
    assert {
        "job_id",
        "node_key",
        "name",
        "storage_key",
        "size_bytes",
        "content_hash",
        "uploaded_at",
    } <= columns
    assert pk is not None
