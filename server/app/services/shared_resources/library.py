"""Authorized library reads, grants, explicit selections and lifecycle guards."""

from __future__ import annotations

from typing import Any

from server.app.jobs.queries.shared_resources.facade import SharedResourceQueries
from server.app.services.job_errors import ConflictError, NotFoundError

from .errors import SharedResourceInUseError, SharedResourceSelectionConflictError
from .records import (
    exact_pin_descriptor,
    grant_record,
    resource_record,
    selection_record,
    version_detail,
    version_summary,
)

_DEFAULT_LIST_LIMIT = 50
_MAX_LIST_LIMIT = 200


class SharedResourceLibraryService:
    def __init__(self, queries: SharedResourceQueries) -> None:
        self._queries = queries

    @staticmethod
    def _not_found() -> NotFoundError:
        return NotFoundError("Shared resource not found")

    def list_resources(self, workspace_id: str, *, limit: int, offset: int) -> dict[str, Any]:
        limit = max(1, min(limit or _DEFAULT_LIST_LIMIT, _MAX_LIST_LIMIT))
        offset = max(0, offset)
        result = self._queries.list_resources(workspace_id, limit, offset)
        return {
            **result,
            "resources": [resource_record(row, workspace_id) for row in result["resources"]],
        }

    def get(self, workspace_id: str, resource_id: str) -> dict[str, Any]:
        row = self._queries.get_resource(workspace_id, resource_id)
        if row is None:
            raise self._not_found()
        return resource_record(row, workspace_id)

    def list_versions(self, workspace_id: str, resource_id: str) -> list[dict[str, Any]]:
        rows = self._queries.list_versions(workspace_id, resource_id)
        if rows is None:
            raise self._not_found()
        return [version_summary(row) for row in rows]

    def get_version(self, workspace_id: str, resource_id: str, version: int) -> dict[str, Any]:
        intent = self._queries.get_visible_version(workspace_id, resource_id, version)
        if intent is None:
            raise self._not_found()
        return version_detail(intent)

    def share(
        self,
        source_workspace_id: str,
        resource_id: str,
        *,
        target_workspace_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        result = self._queries.share(
            source_workspace_id=source_workspace_id,
            resource_id=resource_id,
            target_workspace_id=target_workspace_id,
            actor_id=actor_id,
        )
        if result["status"] == "not_found":
            raise self._not_found()
        return {
            "grant": grant_record(result["grant"]),
            "duplicate": result["status"] == "duplicate",
        }

    def list_grants(self, source_workspace_id: str, resource_id: str) -> list[dict[str, Any]]:
        rows = self._queries.list_grants(source_workspace_id, resource_id)
        if rows is None:
            raise self._not_found()
        return [grant_record(row) for row in rows]

    def revoke(
        self,
        source_workspace_id: str,
        resource_id: str,
        *,
        target_workspace_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        row = self._queries.revoke(
            source_workspace_id=source_workspace_id,
            resource_id=resource_id,
            target_workspace_id=target_workspace_id,
            actor_id=actor_id,
        )
        if row is None:
            raise self._not_found()
        return grant_record(row)

    def select_version(
        self,
        workspace_id: str,
        resource_id: str,
        *,
        version: int,
        expected_revision: int,
        actor_id: str,
    ) -> dict[str, Any]:
        result = self._queries.select_version(
            workspace_id=workspace_id,
            resource_id=resource_id,
            version=version,
            expected_revision=expected_revision,
            actor_id=actor_id,
        )
        status = result["status"]
        if status == "not_found":
            raise self._not_found()
        if status == "revoked":
            raise ConflictError("Shared resource access is revoked for new work")
        if status == "conflict":
            raise SharedResourceSelectionConflictError(int(result["current_revision"]))
        return selection_record(result["selection"])

    def resolve_exact_version(
        self,
        workspace_id: str,
        resource_id: str,
        *,
        version: int,
        manifest_hash: str,
        for_new_run: bool,
    ) -> dict[str, Any]:
        """Resolve an exact pin for M43; internal descriptors include object keys."""
        result = self._queries.resolve_version(
            workspace_id=workspace_id,
            resource_id=resource_id,
            version=version,
            manifest_hash=manifest_hash,
            for_new_run=for_new_run,
        )
        status = result["status"]
        if status == "not_found":
            raise self._not_found()
        if status == "revoked":
            raise ConflictError("Shared resource access is revoked for new work")
        if status == "hash_mismatch":
            raise ConflictError(
                "Shared resource manifest hash does not match the requested version"
            )
        return exact_pin_descriptor(result)

    def delete_version(
        self, owner_workspace_id: str, resource_id: str, *, version: int
    ) -> dict[str, Any]:
        result = self._queries.delete_version(
            owner_workspace_id=owner_workspace_id, resource_id=resource_id, version=version
        )
        status = result["status"]
        if status == "not_found":
            raise self._not_found()
        if status == "in_use":
            raise SharedResourceInUseError(
                int(result["selection_references"]), int(result["job_pin_references"])
            )
        return {
            "deleted": resource_id,
            "version": version,
            "version_id": str(result["version_id"]),
            "physical_objects_deleted": False,
        }
