"""Direct ``job_artifacts`` fixtures independent of production write APIs."""

from __future__ import annotations

from typing import Any

from server.app.db.transaction import write_transaction

_SEED_ARTIFACT_ROW_SQL = """
insert into job_artifacts(
  job_id, node_key, name, storage_key, size_bytes, content_hash
) values (%s, %s, %s, %s, %s, %s)
on conflict (job_id, node_key, name) do update
set storage_key=excluded.storage_key,
    size_bytes=excluded.size_bytes,
    content_hash=excluded.content_hash,
    uploaded_at=current_timestamp
returning *
"""


def seed_artifact_row(
    store: Any,
    *,
    workspace_id: str,
    job_id: str,
    node_key: str,
    name: str,
    storage_key: str,
    size_bytes: int,
    content_hash: str = "",
) -> dict[str, Any]:
    """Seed a pre-existing fixed or versioned authority manifest row."""
    with write_transaction(store.database_dsn) as conn:
        return seed_artifact_row_tx(
            conn,
            workspace_id=workspace_id,
            job_id=job_id,
            node_key=node_key,
            name=name,
            storage_key=storage_key,
            size_bytes=size_bytes,
            content_hash=content_hash,
        )


def seed_artifact_row_tx(
    conn: Any,
    *,
    workspace_id: str,
    job_id: str,
    node_key: str,
    name: str,
    storage_key: str,
    size_bytes: int,
    content_hash: str = "",
) -> dict[str, Any]:
    """Seed a manifest row on the caller's existing transaction."""
    assert storage_key.startswith(f"jobs/{workspace_id}/{job_id}/")
    row = conn.execute(
        _SEED_ARTIFACT_ROW_SQL,
        (job_id, node_key, name, storage_key, size_bytes, content_hash),
    ).fetchone()
    assert row is not None
    return dict(row)
