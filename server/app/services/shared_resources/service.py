"""Single service entry point used by the API assembly."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from server.app.jobs.queries.shared_resources.facade import SharedResourceQueries
from server.app.storage import ObjectStorage

from .creation import SharedResourceCreationService
from .library import SharedResourceLibraryService


class SharedResourcesService:
    def __init__(
        self,
        job_db: Any,
        storage: ObjectStorage | None = None,
        *,
        storage_provider: Callable[[], ObjectStorage | None] | None = None,
    ) -> None:
        queries = SharedResourceQueries(job_db)
        self._creation = SharedResourceCreationService(queries, storage, storage_provider)
        self._library = SharedResourceLibraryService(queries)

    def create_resource(self, workspace_id: str, **kwargs: Any) -> dict[str, Any]:
        return self._creation.create_resource(workspace_id, **kwargs)

    def create_version(self, workspace_id: str, resource_id: str, **kwargs: Any) -> dict[str, Any]:
        return self._creation.create_version(workspace_id, resource_id, **kwargs)

    def list_resources(self, workspace_id: str, *, limit: int, offset: int) -> dict[str, Any]:
        return self._library.list_resources(workspace_id, limit=limit, offset=offset)

    def get(self, workspace_id: str, resource_id: str) -> dict[str, Any]:
        return self._library.get(workspace_id, resource_id)

    def list_versions(self, workspace_id: str, resource_id: str) -> list[dict[str, Any]]:
        return self._library.list_versions(workspace_id, resource_id)

    def get_version(self, workspace_id: str, resource_id: str, version: int) -> dict[str, Any]:
        return self._library.get_version(workspace_id, resource_id, version)

    def share(self, source_workspace_id: str, resource_id: str, **kwargs: Any) -> dict[str, Any]:
        return self._library.share(source_workspace_id, resource_id, **kwargs)

    def list_grants(self, source_workspace_id: str, resource_id: str) -> list[dict[str, Any]]:
        return self._library.list_grants(source_workspace_id, resource_id)

    def revoke(self, source_workspace_id: str, resource_id: str, **kwargs: Any) -> dict[str, Any]:
        return self._library.revoke(source_workspace_id, resource_id, **kwargs)

    def select_version(self, workspace_id: str, resource_id: str, **kwargs: Any) -> dict[str, Any]:
        return self._library.select_version(workspace_id, resource_id, **kwargs)

    def resolve_exact_version(
        self, workspace_id: str, resource_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        return self._library.resolve_exact_version(workspace_id, resource_id, **kwargs)

    def delete_version(
        self, owner_workspace_id: str, resource_id: str, *, version: int
    ) -> dict[str, Any]:
        return self._library.delete_version(owner_workspace_id, resource_id, version=version)
