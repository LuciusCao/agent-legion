"""Authorization-scoped read models; copying/failed intents stay invisible."""

from __future__ import annotations

from typing import Any

_RESOURCE_READ = """
select r.*, g.id as grant_id, g.status as access_status,
  s.revision as selection_revision, s.selected_by, s.updated_at as selected_at,
  cv.id as current_version_id, cv.version as current_version,
  cv.manifest_hash as current_manifest_hash,
  (select count(*) from shared_resource_versions vc
    where vc.resource_id=r.id and vc.status='ready' and vc.deleted_at is null) as version_count
from shared_resources r
join shared_resource_grants g on g.resource_id=r.id and g.workspace_id=%s
left join shared_resource_selections s on s.grant_id=g.id
left join shared_resource_versions cv on cv.id=s.version_id
where r.id=%s
  and exists (select 1 from shared_resource_versions rv
    where rv.resource_id=r.id and rv.status='ready')
"""


def get_resource_tx(conn: Any, workspace_id: str, resource_id: str) -> dict[str, Any] | None:
    row = conn.execute(_RESOURCE_READ, (workspace_id, resource_id)).fetchone()
    return dict(row) if row is not None else None


def list_resources_tx(conn: Any, workspace_id: str, limit: int, offset: int) -> dict[str, Any]:
    total = conn.execute(
        "select count(*) as total from shared_resources r"
        " join shared_resource_grants g on g.resource_id=r.id and g.workspace_id=%s"
        " where exists (select 1 from shared_resource_versions rv"
        " where rv.resource_id=r.id and rv.status='ready')",
        (workspace_id,),
    ).fetchone()
    base = _RESOURCE_READ.replace("where r.id=%s", "where true")
    rows = conn.execute(
        base + " order by r.created_at desc, r.id desc limit %s offset %s",
        (workspace_id, limit, offset),
    ).fetchall()
    return {
        "resources": [dict(row) for row in rows],
        "total": int(total["total"]),
        "limit": limit,
        "offset": offset,
    }


def list_versions_tx(conn: Any, workspace_id: str, resource_id: str) -> list[dict[str, Any]] | None:
    if get_resource_tx(conn, workspace_id, resource_id) is None:
        return None
    rows = conn.execute(
        "select v.*, (select count(*) from shared_resource_files f"
        " where f.version_id=v.id) as file_count,"
        " (select coalesce(sum(f.size_bytes), 0) from shared_resource_files f"
        " where f.version_id=v.id) as total_size_bytes"
        " from shared_resource_versions v where v.resource_id=%s"
        " and v.status='ready' and v.deleted_at is null order by v.version desc",
        (resource_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_visible_version_tx(
    conn: Any, workspace_id: str, resource_id: str, version: int
) -> dict[str, Any] | None:
    if get_resource_tx(conn, workspace_id, resource_id) is None:
        return None
    row = conn.execute(
        "select * from shared_resource_versions where resource_id=%s and version=%s"
        " and status='ready' and deleted_at is null",
        (resource_id, version),
    ).fetchone()
    if row is None:
        return None
    files = conn.execute(
        "select * from shared_resource_files where version_id=%s order by ordinal", (row["id"],)
    ).fetchall()
    return {"version": dict(row), "files": [dict(file) for file in files]}
