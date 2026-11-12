"""Ready-material precheck for inline ``text`` run items (materials-and-runs §4.1).

A text item is persisted through the server-side material write chokepoint
(``services/material_object_publish.py`` + ``jobs/queries/material_intent.py``,
#1314: uploading intent row → PUT → atomic swap to a ready row). This mixin
is the read-only precheck, content-addressed on ``(workspace_id,
content_hash)``:

- no row with that hash → the text is written as a new material;
- an existing **ready** row is reused untouched (same identity as
  re-uploading the same bytes); publication revalidates it under the row
  lock;
- every non-ready row belongs to the upload/TTL protocol, never to inline
  text. Its age/status cannot prove that an outstanding PUT URL is dead.
  The caller rejects the whole batch without changing that row or its key.
"""

from __future__ import annotations

from server.app.jobs.queries.connection import ConnectionQueriesMixin

_LOOKUP_SQL = "select id, status from materials where workspace_id=%s and content_hash=%s"


class InlineMaterialQueriesMixin(ConnectionQueriesMixin):
    def find_material_by_hash(self, workspace_id: str, content_hash: str) -> dict[str, str] | None:
        """``{id, status}`` of the workspace's row for this content hash, or None."""
        with self._connect_read() as conn:
            row = conn.execute(_LOOKUP_SQL, (workspace_id, content_hash)).fetchone()
        return None if row is None else {"id": str(row["id"]), "status": str(row["status"])}
