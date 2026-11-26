"""JobQueries-backed persistence for workspace release manifests (#120).

All SQL and transaction handling stays behind this adjunct to the public
JobQueries facade. The current manifest is the greatest stored revision; no
rows means the immutable, implicit empty revision 0.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable
from typing import Any

from server.app.services.job_artifact_names import canonical_downloadable_artifact_names

_COLUMNS = "id, workspace_id, revision, artifact_names_json, manifest_hash, created_by, created_at"


def release_manifest_document(names: Iterable[str]) -> str:
    return json.dumps(
        canonical_downloadable_artifact_names(names), ensure_ascii=False, separators=(",", ":")
    )


def release_manifest_hash(names: Iterable[str]) -> str:
    return hashlib.sha256(release_manifest_document(names).encode("utf-8")).hexdigest()


def _current_tx(conn: Any, workspace_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        f"select {_COLUMNS} from workspace_release_manifests"
        " where workspace_id=%s order by revision desc limit 1",
        (workspace_id,),
    ).fetchone()
    return dict(row) if row is not None else None


class WorkspaceReleaseManifestQueries:
    def __init__(self, job_db: Any) -> None:
        self._job_db = job_db

    def workspace_exists(self, workspace_id: str) -> bool:
        return self._job_db.get_workspace(workspace_id) is not None

    def get_current(self, workspace_id: str) -> dict[str, Any] | None:
        with self._job_db.read() as conn:
            return _current_tx(conn, workspace_id)

    def list_revisions(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._job_db.read() as conn:
            rows = conn.execute(
                f"select {_COLUMNS} from workspace_release_manifests"
                " where workspace_id=%s order by revision desc",
                (workspace_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def append_revision(
        self,
        *,
        workspace_id: str,
        artifact_names: list[str],
        expected_revision: int,
        created_by: str,
    ) -> dict[str, Any]:
        document = release_manifest_document(artifact_names)
        with self._job_db.write() as conn:
            conn.execute(
                "select pg_advisory_xact_lock(hashtext('release-manifest-write:' || %s))",
                (workspace_id,),
            )
            workspace = conn.execute(
                "select id from workspaces where id=%s for share", (workspace_id,)
            ).fetchone()
            if workspace is None:
                return {"status": "not_found", "manifest": None}
            current = _current_tx(conn, workspace_id)
            current_revision = int(current["revision"]) if current is not None else 0
            if current_revision != expected_revision:
                return {"status": "conflict", "manifest": current}
            if current is None and document == "[]":
                return {"status": "unchanged", "manifest": None}
            if current is not None and str(current["artifact_names_json"]) == document:
                return {"status": "unchanged", "manifest": current}
            row = conn.execute(
                f"insert into workspace_release_manifests({_COLUMNS})"
                " values (%s, %s, %s, %s, %s, %s, current_timestamp)"
                f" returning {_COLUMNS}",
                (
                    uuid.uuid4().hex,
                    workspace_id,
                    current_revision + 1,
                    document,
                    release_manifest_hash(artifact_names),
                    created_by,
                ),
            ).fetchone()
            if row is None:  # pragma: no cover - INSERT RETURNING is atomic
                raise ValueError("release manifest insert did not return a row")
            return {"status": "appended", "manifest": dict(row)}
