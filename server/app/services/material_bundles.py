"""Material bundles service (materials-and-runs design §5, #156).

A bundle is a folder uploaded as one run item: the member files flow
through the regular materials presign/complete upload (content-addressed
dedup intact), and the bundle row is a manifest — member material ids plus
their relative paths. Bundles own no bytes, so deletion only removes the
manifest rows; the two-way delete guard keeps the manifest consistent:
a member material cannot be deleted while referenced (materials service),
and a bundle cannot be deleted while a job input references it (here).
"""

from __future__ import annotations

import uuid
from typing import Any

from psycopg import IntegrityError

from server.app.db.dialect import ConnectSource
from server.app.db.rowmap import iso_optional
from server.app.db.transaction import read_connection, write_transaction
from server.app.services.job_errors import (
    ConflictError,
    InvalidOperationError,
    NotFoundError,
)

_DEFAULT_LIST_LIMIT = 50
_MAX_LIST_LIMIT = 200
#: Manifest fan-out cap: a claim-time bundle presigns one GET URL per member,
#: so an unbounded member count would amplify the claim payload (design §5).
MAX_BUNDLE_MEMBERS = 1000


class BundleInUseError(ConflictError):
    """Bundle is still referenced by a job input (routes map to 409)."""


def validate_member_path(path: str) -> str:
    """Normalize and validate a member's relative path inside the bundle.

    The path is a POSIX-style relative path: no absolute paths, no ``..``
    or empty segments, no backslashes (they would re-interpret as
    separators on Windows-shaped consumers), no control characters (the
    manifest hash joins fields with TAB/LF — a path carrying them would
    make the encoding ambiguous). Returns the stripped path.
    """
    cleaned = path.strip().strip("/")
    if not cleaned:
        raise InvalidOperationError("bundle member path must be a non-empty relative path")
    if path.startswith("/") or "\\" in cleaned:
        raise InvalidOperationError(f"bundle member path must be a relative path: {path!r}")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in cleaned):
        raise InvalidOperationError(
            f"bundle member path must not contain control characters: {path!r}"
        )
    segments = cleaned.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise InvalidOperationError(
            f"bundle member path must not contain empty, '.' or '..' segments: {path!r}"
        )
    return cleaned


def _record(row: dict[str, Any], members: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Public bundle record; member storage keys never leave the service."""
    record = {
        "id": str(row["id"]),
        "workspace_id": str(row["workspace_id"]),
        "name": str(row["name"]),
        "total_size_bytes": int(row["total_size_bytes"]),
        "file_count": int(row["file_count"]),
        "created_by": str(row["created_by"]),
        "created_at": iso_optional(row["created_at"]),
    }
    if members is not None:
        record["members"] = members
    return record


class MaterialBundlesService:
    def __init__(self, database_dsn: ConnectSource) -> None:
        # database_dsn: JobQueries facade or bare DSN (BOUNDARY-DATA-001, #187).
        self._dsn = database_dsn

    def create(
        self,
        workspace_id: str,
        *,
        name: str,
        members: list[dict[str, str]],
        created_by: str = "",
    ) -> dict[str, Any]:
        """Create a bundle manifest from ready materials of this workspace.

        Every member must reference a ready material row of the same
        workspace with a valid, unique relative path; both tables are
        inserted in one transaction so a rejected manifest leaves nothing
        behind.
        """
        name = name.strip()
        if not name:
            raise InvalidOperationError("bundle name is required")
        if not members:
            raise InvalidOperationError("bundle requires at least one member")
        if len(members) > MAX_BUNDLE_MEMBERS:
            raise InvalidOperationError(
                f"bundle has {len(members)} members, exceeding the {MAX_BUNDLE_MEMBERS} limit"
            )
        paths: list[str] = []
        material_ids: list[str] = []
        for member in members:
            material_id = str(member.get("material_id") or "").strip()
            if not material_id:
                raise InvalidOperationError("bundle member requires material_id")
            paths.append(validate_member_path(str(member.get("path") or "")))
            material_ids.append(material_id)
        if len(set(paths)) != len(paths):
            raise InvalidOperationError("bundle member paths must be unique")

        bundle_id = uuid.uuid4().hex
        try:
            with write_transaction(self._dsn) as conn:
                rows = {
                    str(row["id"]): row
                    for row in conn.execute(
                        "select id, status, size_bytes from materials"
                        " where workspace_id=%s and id = any(%s)",
                        (workspace_id, material_ids),
                    ).fetchall()
                }
                for material_id in material_ids:
                    row = rows.get(material_id)
                    if row is None:
                        raise NotFoundError(f"Material not found: {material_id}")
                    if str(row["status"]) != "ready":
                        raise InvalidOperationError(
                            f"Material is not ready: {material_id} (status: {row['status']})"
                        )
                # Snapshot total counts each referenced material once, even
                # when the manifest lists it at several paths.
                total_size = sum(int(rows[mid]["size_bytes"]) for mid in set(material_ids))
                conn.execute(
                    "insert into material_bundles("
                    " id, workspace_id, name, total_size_bytes, file_count, created_by"
                    ") values (%s, %s, %s, %s, %s, %s)",
                    (bundle_id, workspace_id, name, total_size, len(members), created_by),
                )
                conn.executemany(
                    "insert into material_bundle_members(bundle_id, material_id, path, ordinal)"
                    " values (%s, %s, %s, %s)",
                    [
                        (bundle_id, material_id, path, ordinal)
                        # Stable ordinal by sorted path (design §5.4).
                        for ordinal, (material_id, path) in enumerate(
                            sorted(zip(material_ids, paths, strict=True), key=lambda pair: pair[1])
                        )
                    ],
                )
        except IntegrityError as exc:
            # A member material was deleted between the readiness check and
            # the member insert (FK violation); the manifest rolled back.
            raise ConflictError("a bundle member material was deleted concurrently; retry") from exc
        return self.get(workspace_id, bundle_id)

    def _fetch_row(self, workspace_id: str, bundle_id: str) -> dict[str, Any]:
        with read_connection(self._dsn) as conn:
            row = conn.execute(
                "select * from material_bundles where id=%s and workspace_id=%s",
                (bundle_id, workspace_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Material bundle not found: {bundle_id}")
        return dict(row)

    def _members(self, bundle_id: str) -> list[dict[str, Any]]:
        with read_connection(self._dsn) as conn:
            rows = conn.execute(
                "select m.material_id, m.path, m.ordinal,"
                " mat.filename, mat.size_bytes, mat.content_hash, mat.status"
                " from material_bundle_members m"
                " join materials mat on mat.id = m.material_id"
                " where m.bundle_id=%s order by m.ordinal",
                (bundle_id,),
            ).fetchall()
        return [
            {
                "material_id": str(row["material_id"]),
                "path": str(row["path"]),
                "ordinal": int(row["ordinal"]),
                "filename": str(row["filename"]),
                "size_bytes": int(row["size_bytes"]),
                "content_hash": str(row["content_hash"]),
                "status": str(row["status"]),
            }
            for row in rows
        ]

    def list(self, workspace_id: str, *, limit: int, offset: int) -> dict[str, Any]:
        limit = max(1, min(limit or _DEFAULT_LIST_LIMIT, _MAX_LIST_LIMIT))
        offset = max(0, offset)
        with read_connection(self._dsn) as conn:
            total_row = conn.execute(
                "select count(*) as total from material_bundles where workspace_id=%s",
                (workspace_id,),
            ).fetchone()
            rows = conn.execute(
                "select * from material_bundles where workspace_id=%s"
                " order by created_at desc, id desc limit %s offset %s",
                (workspace_id, limit, offset),
            ).fetchall()
        total = int(total_row["total"]) if total_row is not None else 0
        return {
            "bundles": [_record(dict(row)) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def get(self, workspace_id: str, bundle_id: str) -> dict[str, Any]:
        return _record(self._fetch_row(workspace_id, bundle_id), self._members(bundle_id))

    def delete(self, workspace_id: str, bundle_id: str) -> None:
        """Delete the manifest unless a job input references it.

        The check and the delete share one transaction with the bundle row
        FOR UPDATE-locked, serializing against run creation (create_jobs_bulk
        FOR KEY SHARE-locks referenced bundle rows) exactly like the
        materials delete guard. Members go with the row (on delete cascade);
        the member materials themselves are untouched.
        """
        with write_transaction(self._dsn) as conn:
            row = conn.execute(
                "select id from material_bundles where id=%s and workspace_id=%s for update",
                (bundle_id, workspace_id),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Material bundle not found: {bundle_id}")
            referencing = conn.execute(
                "select id from jobs where workspace_id=%s"
                " and input_json::jsonb ->> 'type' = 'bundle'"
                " and input_json::jsonb ->> 'bundle_id' = %s limit 1",
                (workspace_id, bundle_id),
            ).fetchone()
            if referencing is not None:
                raise BundleInUseError(f"Material bundle is referenced by job {referencing['id']}")
            conn.execute("delete from material_bundles where id=%s", (bundle_id,))
