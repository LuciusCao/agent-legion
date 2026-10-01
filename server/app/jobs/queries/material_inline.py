"""Ready-material rows for inline ``text`` run items (materials-and-runs §4.1).

A text item is persisted object-first by ``run_text_items``; this mixin is
the row half, content-addressed on ``(workspace_id, content_hash)``:

- no row with that hash → insert a ready row;
- an existing **ready** row wins untouched (same identity as re-uploading
  the same bytes — the caller reuses its id);
- every non-ready row belongs to the upload/TTL protocol, never to inline
  text. Its age/status cannot prove that an outstanding PUT URL is dead.
  Reject the whole batch without changing that row or its storage key.

``expires_at`` follows the same TTL rule as ``material_ttl.mark_ready``.
"""

from __future__ import annotations

import uuid
from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin
from server.app.services.job_errors import ConflictError

_INSERT_SQL = (
    "insert into materials(id, workspace_id, content_hash, filename, content_type,"
    " size_bytes, storage_key, status, created_by, expires_at)"
    " values (%s, %s, %s, %s, %s, %s, %s, 'ready', %s,"
    " case when %s > 0 then now() + make_interval(days => %s) end)"
    " on conflict (workspace_id, content_hash) where content_hash <> '' do nothing"
    " returning id, status"
)
_LOOKUP_SQL = "select id, status from materials where workspace_id=%s and content_hash=%s"
_BATCH_LOCK_SQL = "select pg_advisory_xact_lock(hashtextextended(%s, 0))"


class InlineMaterialQueriesMixin(ConnectionQueriesMixin):
    def find_material_by_hash(self, workspace_id: str, content_hash: str) -> dict[str, str] | None:
        """``{id, status}`` of the workspace's row for this content hash, or None."""
        with self._connect_read() as conn:
            row = conn.execute(_LOOKUP_SQL, (workspace_id, content_hash)).fetchone()
        return None if row is None else {"id": str(row["id"]), "status": str(row["status"])}

    def publish_inline_materials(
        self,
        workspace_id: str,
        entries: list[dict[str, Any]],
        *,
        created_by: str,
        ttl_days: int,
    ) -> dict[str, str]:
        """Insert or read, never replace; revalidate reuse under the current row lock."""
        result = {}
        with self.write() as conn:
            conn.execute(_BATCH_LOCK_SQL, (f"inline-material:{workspace_id}",))
            for entry in sorted(entries, key=lambda entry: entry["content_hash"]):
                digest = entry["content_hash"]
                expected_id = entry.get("material_id")
                row = None
                if expected_id is None:
                    row = conn.execute(
                        _INSERT_SQL,
                        (
                            uuid.uuid4().hex,
                            workspace_id,
                            digest,
                            entry["filename"],
                            entry["content_type"],
                            entry["size_bytes"],
                            entry["storage_key"],
                            created_by,
                            ttl_days,
                            ttl_days,
                        ),
                    ).fetchone()
                if row is None:
                    row = conn.execute(
                        _LOOKUP_SQL + " for update", (workspace_id, digest)
                    ).fetchone()
                if (
                    row is None
                    or row["status"] != "ready"
                    or (expected_id is not None and row["id"] != expected_id)
                ):
                    raise ConflictError(
                        "Material for text content is not ready or changed; "
                        "finish the upload or remove the non-ready material before retrying"
                    )
                result[digest] = str(row["id"])
        return result

    def referenced_inline_objects(self, workspace_id: str, keys: list[str]) -> set[str]:
        """Protect committed objects when a commit acknowledgement was lost."""
        with self._connect_read() as conn:
            # Wait out any in-flight COMMIT before deciding an object is unreferenced.
            conn.execute(_BATCH_LOCK_SQL, (f"inline-material:{workspace_id}",))
            rows = conn.execute(
                "select storage_key from materials where workspace_id=%s and storage_key=ANY(%s)",
                (workspace_id, keys),
            ).fetchall()
        return {str(row["storage_key"]) for row in rows}
