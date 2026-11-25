"""Idempotent intent → verified copy → atomic publication workflows."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from server.app.jobs.queries.shared_resources.facade import SharedResourceQueries
from server.app.services.job_errors import ConflictError, InvalidOperationError, NotFoundError
from server.app.services.verified_object_copy import ObjectCopySpec, copy_objects_verified
from server.app.storage import ObjectStorage

from .errors import SharedResourceCopyError, SharedResourceStorageUnavailableError
from .manifest import manifest_hash, prepare_source_snapshot, request_fingerprint
from .records import resource_record, version_detail


class SharedResourceCreationService:
    def __init__(
        self,
        queries: SharedResourceQueries,
        storage: ObjectStorage | None,
        storage_provider: Callable[[], ObjectStorage | None] | None = None,
    ) -> None:
        self._queries = queries
        self._storage = storage
        self._storage_provider = storage_provider

    def _require_storage(self) -> ObjectStorage:
        storage = self._storage_provider() if self._storage_provider else self._storage
        if storage is None:
            raise SharedResourceStorageUnavailableError(
                "Shared resource storage is not configured on this instance"
            )
        return storage

    @staticmethod
    def _validate_source(source_type: str, source_id: str) -> None:
        if source_type not in {"material", "bundle"}:
            raise InvalidOperationError("Shared resource source type must be material or bundle")
        if not source_id.strip():
            raise InvalidOperationError("Shared resource source id is required")

    def create_resource(
        self,
        workspace_id: str,
        *,
        name: str,
        source_type: str,
        source_id: str,
        idempotency_key: str,
        created_by: str,
    ) -> dict[str, Any]:
        storage = self._require_storage()
        name = name.strip()
        idempotency_key = idempotency_key.strip()
        if not name:
            raise InvalidOperationError("Shared resource name is required")
        if not idempotency_key:
            raise InvalidOperationError("idempotency_key is required")
        self._validate_source(source_type, source_id)
        fingerprint = request_fingerprint(
            workspace_id=workspace_id, source_type=source_type, source_id=source_id, name=name
        )
        existing = self._queries.get_resource_creation(workspace_id, idempotency_key)
        if existing is not None:
            if str(existing["resource"]["create_request_hash"]) != fingerprint:
                raise ConflictError("idempotency_key was already used for a different resource")
            return self._complete_resource_intent(existing)
        snapshot = prepare_source_snapshot(
            self._queries.load_source_snapshot(workspace_id, source_type, source_id), storage
        )
        intent = self._queries.create_resource_intent(
            workspace_id=workspace_id,
            name=name,
            token=idempotency_key,
            request_hash=fingerprint,
            manifest_hash=manifest_hash(snapshot["files"]),
            source_snapshot=snapshot,
            created_by=created_by,
        )
        if str(intent["resource"]["create_request_hash"]) != fingerprint:
            raise ConflictError("idempotency_key was already used for a different resource")
        return self._complete_resource_intent(intent)

    def create_version(
        self,
        workspace_id: str,
        resource_id: str,
        *,
        source_type: str,
        source_id: str,
        idempotency_key: str,
        created_by: str,
    ) -> dict[str, Any]:
        storage = self._require_storage()
        idempotency_key = idempotency_key.strip()
        if not idempotency_key:
            raise InvalidOperationError("idempotency_key is required")
        self._validate_source(source_type, source_id)
        resource = self._queries.get_resource(workspace_id, resource_id)
        if resource is None or str(resource["owner_workspace_id"]) != workspace_id:
            raise NotFoundError("Shared resource not found")
        fingerprint = request_fingerprint(
            workspace_id=workspace_id,
            resource_id=resource_id,
            source_type=source_type,
            source_id=source_id,
        )
        existing = self._queries.get_version_creation(resource_id, idempotency_key)
        if existing is not None:
            if str(existing["version"]["request_hash"]) != fingerprint:
                raise ConflictError("idempotency_key was already used for a different version")
            return self._complete_version_intent(workspace_id, resource_id, existing)
        snapshot = prepare_source_snapshot(
            self._queries.load_source_snapshot(workspace_id, source_type, source_id), storage
        )
        intent = self._queries.create_version_intent(
            resource_id=resource_id,
            token=idempotency_key,
            request_hash=fingerprint,
            manifest_hash=manifest_hash(snapshot["files"]),
            source_snapshot=snapshot,
            created_by=created_by,
        )
        if intent is None:
            raise NotFoundError("Shared resource not found")
        if str(intent["version"]["request_hash"]) != fingerprint:
            raise ConflictError("idempotency_key was already used for a different version")
        return self._complete_version_intent(workspace_id, resource_id, intent)

    @staticmethod
    def _copies_match(files: list[dict[str, Any]], copies: tuple[Any, ...]) -> bool:
        expected = {
            str(file["storage_key"]): (
                str(file["source_storage_key"]),
                int(file["size_bytes"]),
                str(file["sha256"]),
            )
            for file in files
        }
        actual = {
            str(copy.destination_key): (
                str(copy.source_key),
                int(copy.size_bytes),
                str(copy.sha256),
            )
            for copy in copies
        }
        return len(copies) == len(files) and actual == expected

    def _copy_and_publish(self, intent: dict[str, Any]) -> dict[str, Any]:
        storage = self._require_storage()
        version = intent["version"]
        if str(version["status"]) != "ready":
            restarted = self._queries.restart_version_attempt(str(version["id"]))
            if restarted is None:
                raise ConflictError("Shared resource creation attempt disappeared; retry")
            intent = restarted
            version = intent["version"]
        if str(version["status"]) == "ready":
            return intent
        generation = int(version["copy_generation"])
        files = intent["files"]
        specs = [
            ObjectCopySpec(
                source_key=str(file["source_storage_key"]),
                destination_key=str(file["storage_key"]),
                source_etag=str(file["source_etag"]),
                expected_size_bytes=int(file["size_bytes"]),
                expected_sha256=str(file["sha256"]),
            )
            for file in files
        ]
        result = copy_objects_verified(storage, specs)
        if not result.ok:
            failure = result.failure
            if failure is None:
                raise RuntimeError("verified copy returned not-ok without a failure")
            index = failure.copy_index
            path = str(files[index]["path"]) if 0 <= index < len(files) else "unknown"
            self._queries.mark_version_failed(
                str(version["id"]), generation, str(failure.code), failure.detail
            )
            raise SharedResourceCopyError(str(failure.code), path)
        if not self._copies_match(files, result.copies):
            detail = "verified copy result does not match the registered file intents"
            self._queries.mark_version_failed(
                str(version["id"]), generation, "verification_failed", detail
            )
            raise SharedResourceCopyError("verification_failed", "manifest")
        published = self._queries.publish_version(str(version["id"]), generation)
        if published is None:
            self._queries.mark_version_failed(
                str(version["id"]),
                generation,
                "publication_conflict",
                "attempt superseded or source snapshot changed",
            )
            raise ConflictError(
                "Shared resource creation was superseded or its source changed; "
                "retry with the same idempotency_key"
            )
        return published

    def _complete_resource_intent(self, intent: dict[str, Any]) -> dict[str, Any]:
        resource_id = str(intent["version"]["resource_id"])
        workspace_id = str(intent["resource"]["owner_workspace_id"])
        self._copy_and_publish(intent)
        resource = self._queries.get_resource(workspace_id, resource_id)
        if resource is None:
            raise NotFoundError("Shared resource not found")
        return resource_record(resource, workspace_id)

    def _complete_version_intent(
        self, workspace_id: str, resource_id: str, intent: dict[str, Any]
    ) -> dict[str, Any]:
        completed = self._copy_and_publish(intent)
        version = int(completed["version"]["version"])
        visible = self._queries.get_visible_version(workspace_id, resource_id, version)
        if visible is None:
            raise NotFoundError("Shared resource version not found")
        return version_detail(visible)
