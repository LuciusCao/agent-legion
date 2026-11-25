"""Workspace-scoped shared resource library API."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.auth.workspace_access import (
    require_additional_workspace_editor,
    require_workspace_access,
    require_workspace_read_access,
)
from server.app.routes.job_http import raise_job_http_error
from server.app.routes.shared_resource_contracts import (
    SharedResourceCreateRequest,
    SharedResourceDeleteVersionResponse,
    SharedResourceGrantListResponse,
    SharedResourceGrantResponse,
    SharedResourceListResponse,
    SharedResourceResponse,
    SharedResourceSelectionRequest,
    SharedResourceSelectionResponse,
    SharedResourceShareRequest,
    SharedResourceShareResponse,
    SharedResourceVersionCreateRequest,
    SharedResourceVersionListResponse,
    SharedResourceVersionResponse,
)
from server.app.services.job_errors import JobServiceError
from server.app.services.shared_resources.errors import (
    SharedResourceCopyError,
    SharedResourceStorageUnavailableError,
)
from server.app.services.shared_resources.service import SharedResourcesService


def _mapped_errors(function: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except SharedResourceStorageUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except SharedResourceCopyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return wrapped


def create_shared_resources_router(service: SharedResourcesService) -> APIRouter:
    router = APIRouter()
    effecting = [Depends(reject_studio_agent_scope)]

    @router.post(
        "/workspaces/{workspace_id}/shared-resources",
        response_model=SharedResourceResponse,
        dependencies=effecting,
    )
    @_mapped_errors
    def create_resource(
        workspace_id: str,
        payload: SharedResourceCreateRequest,
        user: Annotated[dict[str, Any], Depends(require_workspace_access)],
    ) -> SharedResourceResponse:
        resource = service.create_resource(
            workspace_id,
            name=payload.name,
            source_type=payload.source.type,
            source_id=payload.source.id,
            idempotency_key=payload.idempotency_key,
            created_by=str(user.get("id") or ""),
        )
        return SharedResourceResponse.model_validate({"resource": resource})

    @router.get(
        "/workspaces/{workspace_id}/shared-resources",
        response_model=SharedResourceListResponse,
    )
    @_mapped_errors
    def list_resources(
        workspace_id: str,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> SharedResourceListResponse:
        return SharedResourceListResponse.model_validate(
            service.list_resources(workspace_id, limit=limit, offset=offset)
        )

    @router.get(
        "/workspaces/{workspace_id}/shared-resources/{resource_id}",
        response_model=SharedResourceResponse,
    )
    @_mapped_errors
    def get_resource(workspace_id: str, resource_id: str) -> SharedResourceResponse:
        return SharedResourceResponse.model_validate(
            {"resource": service.get(workspace_id, resource_id)}
        )

    @router.post(
        "/workspaces/{workspace_id}/shared-resources/{resource_id}/versions",
        response_model=SharedResourceVersionResponse,
        dependencies=effecting,
    )
    @_mapped_errors
    def create_version(
        workspace_id: str,
        resource_id: str,
        payload: SharedResourceVersionCreateRequest,
        user: Annotated[dict[str, Any], Depends(require_workspace_access)],
    ) -> SharedResourceVersionResponse:
        version = service.create_version(
            workspace_id,
            resource_id,
            source_type=payload.source.type,
            source_id=payload.source.id,
            idempotency_key=payload.idempotency_key,
            created_by=str(user.get("id") or ""),
        )
        return SharedResourceVersionResponse.model_validate({"version": version})

    @router.get(
        "/workspaces/{workspace_id}/shared-resources/{resource_id}/versions",
        response_model=SharedResourceVersionListResponse,
    )
    @_mapped_errors
    def list_versions(workspace_id: str, resource_id: str) -> SharedResourceVersionListResponse:
        return SharedResourceVersionListResponse.model_validate(
            {"versions": service.list_versions(workspace_id, resource_id)}
        )

    @router.get(
        "/workspaces/{workspace_id}/shared-resources/{resource_id}/versions/{version}",
        response_model=SharedResourceVersionResponse,
    )
    @_mapped_errors
    def get_version(
        workspace_id: str, resource_id: str, version: int
    ) -> SharedResourceVersionResponse:
        return SharedResourceVersionResponse.model_validate(
            {"version": service.get_version(workspace_id, resource_id, version)}
        )

    @router.put(
        "/workspaces/{workspace_id}/shared-resources/{resource_id}/selection",
        response_model=SharedResourceSelectionResponse,
        dependencies=effecting,
    )
    @_mapped_errors
    def select_version(
        workspace_id: str,
        resource_id: str,
        payload: SharedResourceSelectionRequest,
        user: Annotated[dict[str, Any], Depends(require_workspace_access)],
    ) -> SharedResourceSelectionResponse:
        selection = service.select_version(
            workspace_id,
            resource_id,
            version=payload.version,
            expected_revision=payload.expected_revision,
            actor_id=str(user.get("id") or ""),
        )
        return SharedResourceSelectionResponse.model_validate({"selection": selection})

    @router.get(
        "/workspaces/{workspace_id}/shared-resources/{resource_id}/shares",
        response_model=SharedResourceGrantListResponse,
    )
    @_mapped_errors
    def list_grants(workspace_id: str, resource_id: str) -> SharedResourceGrantListResponse:
        return SharedResourceGrantListResponse.model_validate(
            {"grants": service.list_grants(workspace_id, resource_id)}
        )

    @router.delete(
        "/workspaces/{workspace_id}/shared-resources/{resource_id}/shares/{target_workspace_id}",
        response_model=SharedResourceGrantResponse,
        dependencies=effecting,
    )
    @_mapped_errors
    def revoke_share(
        workspace_id: str,
        resource_id: str,
        target_workspace_id: str,
        user: Annotated[dict[str, Any], Depends(require_workspace_access)],
    ) -> SharedResourceGrantResponse:
        grant = service.revoke(
            workspace_id,
            resource_id,
            target_workspace_id=target_workspace_id,
            actor_id=str(user.get("id") or ""),
        )
        return SharedResourceGrantResponse.model_validate({"grant": grant})

    @router.delete(
        "/workspaces/{workspace_id}/shared-resources/{resource_id}/versions/{version}",
        response_model=SharedResourceDeleteVersionResponse,
        dependencies=effecting,
    )
    @_mapped_errors
    def delete_version(
        workspace_id: str, resource_id: str, version: int
    ) -> SharedResourceDeleteVersionResponse:
        return SharedResourceDeleteVersionResponse.model_validate(
            service.delete_version(workspace_id, resource_id, version=version)
        )

    return router


def create_shared_resource_share_router(service: SharedResourcesService) -> APIRouter:
    """Mount outside secured(): source-read and target-write replace source-write."""
    router = APIRouter()

    @router.post(
        "/workspaces/{workspace_id}/shared-resources/{resource_id}/shares",
        response_model=SharedResourceShareResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    @_mapped_errors
    def share_resource(
        workspace_id: str,
        resource_id: str,
        payload: SharedResourceShareRequest,
        request: Request,
        user: Annotated[dict[str, Any], Depends(require_workspace_read_access)],
    ) -> SharedResourceShareResponse:
        require_additional_workspace_editor(payload.target_workspace_id, request, user)
        result = service.share(
            workspace_id,
            resource_id,
            target_workspace_id=payload.target_workspace_id,
            actor_id=str(user.get("id") or ""),
        )
        return SharedResourceShareResponse.model_validate(result)

    return router
