"""Schema v52: materials table (materials-and-runs design §5.1).

Materials hold browser-upload metadata; bytes live in the instance
S3-compatible object store. (workspace_id, content_hash) dedups uploads via a
partial unique index over declared hashes ('' rows never collide). The
latest-migration record pin moved to tests/db/test_material_bundles_schema.py
(v55).
"""

from __future__ import annotations

from server.app.db.transaction import read_connection
from tests.postgres_support import TEST_DATABASE_URL


def _indexdef(conn, name: str) -> str:
    row = conn.execute(
        "select indexdef from pg_indexes where schemaname=current_schema() and indexname=%s",
        (name,),
    ).fetchone()
    return str(row["indexdef"]) if row is not None else ""


def test_materials_table_shape() -> None:
    # The autouse fixture already ran init_db at the current SCHEMA_VERSION.
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = {
            str(row["column_name"])
            for row in conn.execute(
                "select column_name from information_schema.columns"
                " where table_schema=current_schema() and table_name='materials'"
            ).fetchall()
        }
        dedup_index = _indexdef(conn, "idx_materials_workspace_content_hash")
    assert {
        "id",
        "workspace_id",
        "content_hash",
        "filename",
        "content_type",
        "size_bytes",
        "storage_key",
        "status",
        "created_by",
        "created_at",
        "expires_at",
    } <= columns
    assert "UNIQUE" in dedup_index
    assert "(workspace_id, content_hash)" in dedup_index
    assert "content_hash <> ''" in dedup_index
