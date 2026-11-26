"""Workspace release manifest reads and append-only revision updates (#120)."""

from __future__ import annotations

import json
from typing import Any

from server.app.db.rowmap import iso_optional
from server.app.jobs.queries.workspace_release_manifests import (
    WorkspaceReleaseManifestQueries,
    release_manifest_hash,
)
from server.app.services.job_artifact_names import canonical_downloadable_artifact_names
from server.app.services.job_errors import ConflictError, InvalidOperationError, NotFoundError


class WorkspaceReleaseManifestConflictError(ConflictError):
    def __init__(self, expected_revision: int, current: dict[str, Any]) -> None:
        self.payload = {
            "message": "Release manifest revision conflict",
            "expected_revision": expected_revision,
            "current_manifest": current,
        }
        super().__init__(self.payload["message"])


def _record(workspace_id: str, row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {
            "workspace_id": workspace_id,
            "revision": 0,
            "artifact_names": [],
            "manifest_hash": release_manifest_hash([]),
            "created_by": "",
            "created_at": None,
        }
    names = json.loads(str(row["artifact_names_json"]))
    return {
        "workspace_id": str(row["workspace_id"]),
        "revision": int(row["revision"]),
        "artifact_names": names,
        "manifest_hash": str(row["manifest_hash"]),
        "created_by": str(row["created_by"]),
        "created_at": iso_optional(row["created_at"]),
    }


def _validated_names(names: list[str]) -> list[str]:
    try:
        return canonical_downloadable_artifact_names(names)
    except ValueError as exc:
        raise InvalidOperationError(str(exc)) from exc


class WorkspaceReleaseManifestsService:
    def __init__(self, job_db: Any) -> None:
        self._queries = WorkspaceReleaseManifestQueries(job_db)

    def _require_workspace(self, workspace_id: str) -> None:
        if not self._queries.workspace_exists(workspace_id):
            raise NotFoundError("Workspace not found")

    def get_current(self, workspace_id: str) -> dict[str, Any]:
        self._require_workspace(workspace_id)
        return _record(workspace_id, self._queries.get_current(workspace_id))

    def list_revisions(self, workspace_id: str) -> list[dict[str, Any]]:
        self._require_workspace(workspace_id)
        return [_record(workspace_id, row) for row in self._queries.list_revisions(workspace_id)]

    def update(
        self,
        workspace_id: str,
        *,
        artifact_names: list[str],
        expected_revision: int,
        created_by: str,
    ) -> dict[str, Any]:
        names = _validated_names(artifact_names)
        result = self._queries.append_revision(
            workspace_id=workspace_id,
            artifact_names=names,
            expected_revision=expected_revision,
            created_by=created_by,
        )
        status = result["status"]
        if status == "not_found":
            raise NotFoundError("Workspace not found")
        current = _record(workspace_id, result["manifest"])
        if status == "conflict":
            raise WorkspaceReleaseManifestConflictError(expected_revision, current)
        return current
