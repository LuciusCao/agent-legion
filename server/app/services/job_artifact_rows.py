"""``job_artifacts`` manifest-row upsert mechanics.

Split from ``job_artifact_objects`` for the file-size budget. The SQL literal
stays in the owning module (BOUNDARY-DATA-001: every DB-touching literal is
registered there); this module owns only the in-transaction row shape shared
by the single upsert and the atomic batch ``record_remote_many``.
"""

from __future__ import annotations

from typing import Any


def upsert_artifact_row_tx(
    conn: Any,
    upsert_sql: str,
    *,
    job_id: str,
    node_key: str,
    name: str,
    storage_key: str,
    size_bytes: int,
    content_hash: str,
) -> dict[str, Any]:
    """Single upsert inside an already-open transaction (the caller owns the
    transaction and the SQL)."""
    row = conn.execute(
        upsert_sql,
        (job_id, node_key, name, storage_key, size_bytes, content_hash),
    ).fetchone()
    assert row is not None
    return dict(row)
