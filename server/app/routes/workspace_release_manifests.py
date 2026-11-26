"""Workspace release manifest current/history reads and CAS updates (#120)."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.auth.workspace_access import require_workspace_access
from server.app.routes.job_http import raise_job_http_error
from server.app.routes.workspace_release_manifest_contracts import (
    WorkspaceReleaseManifestConflictResponse,
    WorkspaceReleaseManifestHistoryResponse,
    WorkspaceReleaseManifestRecord,
    WorkspaceReleaseManifestUpdateRequest,
)
from server.app.services.job_errors import JobServiceError
from server.app.services.workspace_release_manifests import WorkspaceReleaseManifestsService


def _mapped_errors(function: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return wrapped


def create_workspace_release_manifests_router(
    service: WorkspaceReleaseManifestsService,
) -> APIRouter:
    router = APIRouter(tags=["workspace-release-manifest"])

    @router.get(
        "/workspaces/{workspace_id}/release-manifest",
        response_model=WorkspaceReleaseManifestRecord,
    )
    @_mapped_errors
    def get_workspace_release_manifest(workspace_id: str) -> WorkspaceReleaseManifestRecord:
        return WorkspaceReleaseManifestRecord.model_validate(service.get_current(workspace_id))

    @router.put(
        "/workspaces/{workspace_id}/release-manifest",
        response_model=WorkspaceReleaseManifestRecord,
        dependencies=[Depends(reject_studio_agent_scope)],
        responses={409: {"model": WorkspaceReleaseManifestConflictResponse}},
    )
    @_mapped_errors
    def update_workspace_release_manifest(
        workspace_id: str,
        payload: WorkspaceReleaseManifestUpdateRequest,
        user: Annotated[dict[str, Any], Depends(require_workspace_access)],
    ) -> WorkspaceReleaseManifestRecord:
        manifest = service.update(
            workspace_id,
            artifact_names=payload.artifact_names,
            expected_revision=payload.expected_revision,
            created_by=str(user.get("id") or ""),
        )
        return WorkspaceReleaseManifestRecord.model_validate(manifest)

    @router.get(
        "/workspaces/{workspace_id}/release-manifest/revisions",
        response_model=WorkspaceReleaseManifestHistoryResponse,
    )
    @_mapped_errors
    def list_workspace_release_manifest_revisions(
        workspace_id: str,
    ) -> WorkspaceReleaseManifestHistoryResponse:
        return WorkspaceReleaseManifestHistoryResponse.model_validate(
            {"revisions": service.list_revisions(workspace_id)}
        )

    return router
