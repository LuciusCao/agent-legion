"""Workspace materials API (materials-and-runs design §6.4).

Upload protocol: presign (row + presigned PUT URL) → browser PUTs directly
to the object store → complete (server-side size/hash verification). Every
endpoint passes require_workspace_access via the secured() route group.
"""

from typing import Annotated, Any, Never

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.auth.workspace_access import require_workspace_access
from server.app.routes.job_http import raise_job_http_error
from server.app.routes.material_bundles import create_material_bundles_router
from server.app.services.job_errors import JobServiceError
from server.app.services.material_bundles import MaterialBundlesService
from server.app.services.materials import (
    MaterialInUseError,
    MaterialsService,
    MaterialStorageUnavailableError,
    MaterialVerificationError,
)


class MaterialPresignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=512)
    size_bytes: int = Field(ge=0)
    content_hash: str | None = None
    content_type: str = Field(default="", max_length=255)


class MaterialRecord(BaseModel):
    id: str
    workspace_id: str
    content_hash: str
    filename: str
    content_type: str
    size_bytes: int
    status: str
    created_by: str
    created_at: str | None
    expires_at: str | None


class MaterialPresignResponse(BaseModel):
    material: MaterialRecord
    upload_url: str | None
    upload_expires_in_seconds: int
    deduplicated: bool


class MaterialResponse(BaseModel):
    material: MaterialRecord


class MaterialListResponse(BaseModel):
    materials: list[MaterialRecord]
    total: int
    limit: int
    offset: int


class MaterialDeleteResponse(BaseModel):
    deleted: str


def _raise_material_http_error(error: JobServiceError) -> Never:
    if isinstance(error, MaterialStorageUnavailableError):
        raise HTTPException(status_code=503, detail=str(error)) from error
    if isinstance(error, MaterialVerificationError):
        raise HTTPException(status_code=422, detail=str(error)) from error
    if isinstance(error, MaterialInUseError):
        raise HTTPException(status_code=409, detail=str(error)) from error
    raise_job_http_error(error)


def create_materials_router(service: MaterialsService) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/workspaces/{workspace_id}/materials/presign",
        response_model=MaterialPresignResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def presign_material(
        workspace_id: str,
        payload: MaterialPresignRequest,
        user: Annotated[dict[str, Any], Depends(require_workspace_access)],
    ) -> MaterialPresignResponse:
        try:
            result = service.presign(
                workspace_id,
                filename=payload.filename,
                size_bytes=payload.size_bytes,
                content_type=payload.content_type,
                content_hash=payload.content_hash or "",
                created_by=str(user.get("id") or ""),
            )
        except JobServiceError as exc:
            _raise_material_http_error(exc)
        return MaterialPresignResponse(**result)

    @router.post(
        "/workspaces/{workspace_id}/materials/{material_id}/complete",
        response_model=MaterialResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def complete_material(workspace_id: str, material_id: str) -> MaterialResponse:
        try:
            return MaterialResponse(
                material=MaterialRecord(**service.complete(workspace_id, material_id))
            )
        except JobServiceError as exc:
            _raise_material_http_error(exc)

    @router.get(
        "/workspaces/{workspace_id}/materials",
        response_model=MaterialListResponse,
    )
    def list_materials(
        workspace_id: str,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> MaterialListResponse:
        try:
            return MaterialListResponse(**service.list(workspace_id, limit=limit, offset=offset))
        except JobServiceError as exc:
            _raise_material_http_error(exc)

    @router.get(
        "/workspaces/{workspace_id}/materials/{material_id}",
        response_model=MaterialResponse,
    )
    def get_material(workspace_id: str, material_id: str) -> MaterialResponse:
        try:
            return MaterialResponse(
                material=MaterialRecord(**service.get(workspace_id, material_id))
            )
        except JobServiceError as exc:
            _raise_material_http_error(exc)

    @router.delete(
        "/workspaces/{workspace_id}/materials/{material_id}",
        response_model=MaterialDeleteResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def delete_material(workspace_id: str, material_id: str) -> MaterialDeleteResponse:
        try:
            service.delete(workspace_id, material_id)
        except JobServiceError as exc:
            _raise_material_http_error(exc)
        return MaterialDeleteResponse(deleted=material_id)

    # Bundle manifests are a materials adjunct (#156): same secured group,
    # same DSN — mounting here keeps the exempt routes/__init__.py untouched.
    bundles = create_material_bundles_router(MaterialBundlesService(service.connect_source))
    router.include_router(bundles)
    return router
