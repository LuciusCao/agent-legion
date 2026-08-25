"""Workspace material bundles API (materials-and-runs design §5, #156).

A bundle is a folder uploaded as one run item: member files flow through the
regular materials presign/complete upload, then one create call here freezes
the manifest (member material ids + relative paths). Every endpoint passes
require_workspace_access via the secured() route group.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.auth.workspace_access import require_workspace_access
from server.app.routes.job_http import raise_job_http_error
from server.app.services.job_errors import JobServiceError
from server.app.services.material_bundles import (
    MAX_BUNDLE_MEMBERS,
    MaterialBundlesService,
)


class MaterialBundleMemberInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_id: str = Field(min_length=1)
    path: str = Field(min_length=1, max_length=1024)


class MaterialBundleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=512)
    members: list[MaterialBundleMemberInput] = Field(min_length=1, max_length=MAX_BUNDLE_MEMBERS)


class MaterialBundleMemberRecord(BaseModel):
    material_id: str
    path: str
    ordinal: int
    filename: str
    size_bytes: int
    content_hash: str
    status: str


class MaterialBundleRecord(BaseModel):
    id: str
    workspace_id: str
    name: str
    total_size_bytes: int
    file_count: int
    created_by: str
    created_at: str | None
    members: list[MaterialBundleMemberRecord] | None = None


class MaterialBundleResponse(BaseModel):
    bundle: MaterialBundleRecord


class MaterialBundleListResponse(BaseModel):
    bundles: list[MaterialBundleRecord]
    total: int
    limit: int
    offset: int


class MaterialBundleDeleteResponse(BaseModel):
    deleted: str


def create_material_bundles_router(service: MaterialBundlesService) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/workspaces/{workspace_id}/material-bundles",
        response_model=MaterialBundleResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def create_bundle(
        workspace_id: str,
        payload: MaterialBundleCreateRequest,
        user: Annotated[dict[str, Any], Depends(require_workspace_access)],
    ) -> MaterialBundleResponse:
        try:
            result = service.create(
                workspace_id,
                name=payload.name,
                members=[member.model_dump() for member in payload.members],
                created_by=str(user.get("id") or ""),
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return MaterialBundleResponse(bundle=MaterialBundleRecord(**result))

    @router.get(
        "/workspaces/{workspace_id}/material-bundles",
        response_model=MaterialBundleListResponse,
    )
    def list_bundles(
        workspace_id: str,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> MaterialBundleListResponse:
        try:
            return MaterialBundleListResponse(
                **service.list(workspace_id, limit=limit, offset=offset)
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.get(
        "/workspaces/{workspace_id}/material-bundles/{bundle_id}",
        response_model=MaterialBundleResponse,
    )
    def get_bundle(workspace_id: str, bundle_id: str) -> MaterialBundleResponse:
        try:
            return MaterialBundleResponse(
                bundle=MaterialBundleRecord(**service.get(workspace_id, bundle_id))
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.delete(
        "/workspaces/{workspace_id}/material-bundles/{bundle_id}",
        response_model=MaterialBundleDeleteResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def delete_bundle(workspace_id: str, bundle_id: str) -> MaterialBundleDeleteResponse:
        try:
            service.delete(workspace_id, bundle_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return MaterialBundleDeleteResponse(deleted=bundle_id)

    return router
