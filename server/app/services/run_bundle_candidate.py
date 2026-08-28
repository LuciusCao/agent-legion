"""Resolve a ``bundle`` run item into a job candidate (#156).

Kept out of ``run_service.py`` for the file-size budget; the validation
mirrors the material candidate: the bundle must exist in this workspace and
every member material must be ready (fail-closed before the first write).
"""

from __future__ import annotations

from typing import Any

from server.app.db.transaction import read_connection
from server.app.services.job_errors import InvalidOperationError, NotFoundError


def bundle_candidate(
    connect_source: Any, workspace_id: str, item: dict[str, Any]
) -> dict[str, Any]:
    """The run candidate for a bundle item (entity dedups on the bundle id).

    ``connect_source`` is the JobQueries facade (or a bare DSN, tests)
    — BOUNDARY-DATA-001, #187.
    """
    bundle_id = str(item.get("bundle_id") or "").strip()
    if not bundle_id:
        raise InvalidOperationError("bundle item requires bundle_id")
    with read_connection(connect_source) as conn:
        row = conn.execute(
            "select b.id, b.name, b.file_count,"
            " (select count(*) from material_bundle_members m"
            "  join materials mat on mat.id = m.material_id"
            "  where m.bundle_id = b.id and mat.status = 'ready') as ready_count"
            " from material_bundles b where b.id=%s and b.workspace_id=%s",
            (bundle_id, workspace_id),
        ).fetchone()
    if row is None:
        raise NotFoundError(f"Material bundle not found: {bundle_id}")
    ready_count = int(row["ready_count"])
    if ready_count != int(row["file_count"]):
        raise InvalidOperationError(
            f"Material bundle is not fully ready: {bundle_id}"
            f" ({ready_count}/{row['file_count']} members ready)"
        )
    return {
        "entity_type": "bundle",
        "entity_id": bundle_id,
        "title": str(row["name"]),
        "stem": "",
        "input": dict(item),
    }
