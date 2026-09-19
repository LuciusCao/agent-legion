"""Ready-material rows for inline ``text`` run items (materials-and-runs §4.1).

A text item is persisted object-first by ``run_text_items``; this mixin is
the row half, content-addressed on ``(workspace_id, content_hash)``:

- no row with that hash → insert a ready row;
- an existing **ready** row wins untouched (same identity as re-uploading
  the same bytes — the caller reuses its id);
- a stale ``uploading`` / ``failed`` / ``expired`` row with that hash (an
  abandoned presign whose object may never have existed) is re-pointed at
  the freshly written object and flipped to ready.

``expires_at`` follows the same TTL rule as ``material_ttl.mark_ready``.
"""

from __future__ import annotations

import uuid

from server.app.jobs.queries.connection import ConnectionQueriesMixin

_UPSERT_SQL = (
    "insert into materials(id, workspace_id, content_hash, filename, content_type,"
    " size_bytes, storage_key, status, created_by, expires_at)"
    " values (%s, %s, %s, %s, %s, %s, %s, 'ready', %s,"
    " case when %s > 0 then now() + make_interval(days => %s) end)"
    " on conflict (workspace_id, content_hash) where content_hash <> '' do update set"
    " filename=excluded.filename, content_type=excluded.content_type,"
    " size_bytes=excluded.size_bytes, storage_key=excluded.storage_key,"
    " status='ready', expires_at=excluded.expires_at"
    " where materials.status <> 'ready'"
    " returning id"
)
_LOOKUP_SQL = "select id, status from materials where workspace_id=%s and content_hash=%s"


class InlineMaterialQueriesMixin(ConnectionQueriesMixin):
    def find_material_by_hash(self, workspace_id: str, content_hash: str) -> dict[str, str] | None:
        """``{id, status}`` of the workspace's row for this content hash, or None."""
        with self._connect_read() as conn:
            row = conn.execute(_LOOKUP_SQL, (workspace_id, content_hash)).fetchone()
        return None if row is None else {"id": str(row["id"]), "status": str(row["status"])}

    def upsert_ready_material(
        self,
        workspace_id: str,
        *,
        content_hash: str,
        filename: str,
        content_type: str,
        size_bytes: int,
        storage_key: str,
        created_by: str,
        ttl_days: int,
    ) -> str:
        """Insert-or-reuse a ready material for already-stored bytes; returns its id."""
        with self.write() as conn:
            row = conn.execute(
                _UPSERT_SQL,
                (
                    uuid.uuid4().hex,
                    workspace_id,
                    content_hash,
                    filename,
                    content_type,
                    size_bytes,
                    storage_key,
                    created_by,
                    ttl_days,
                    ttl_days,
                ),
            ).fetchone()
            if row is None:
                # WHERE excluded the update: an existing ready row wins.
                row = conn.execute(_LOOKUP_SQL, (workspace_id, content_hash)).fetchone()
        if row is None:
            raise RuntimeError(f"materials upsert lost its row for hash {content_hash}")
        return str(row["id"])
