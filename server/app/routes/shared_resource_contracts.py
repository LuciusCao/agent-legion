"""Transport contracts for the shared resource library."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SharedResourceSourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["material", "bundle"]
    id: str = Field(min_length=1, max_length=256)


class SharedResourceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=512)
    source: SharedResourceSourceInput
    idempotency_key: str = Field(min_length=1, max_length=256)


class SharedResourceVersionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: SharedResourceSourceInput
    idempotency_key: str = Field(min_length=1, max_length=256)


class SharedResourceShareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_workspace_id: str = Field(min_length=1, max_length=256)


class SharedResourceSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    expected_revision: int = Field(ge=1)


class SharedResourceSourceMaterialRecord(BaseModel):
    material_id: str
    path: str
    upload_generation: int
    sha256: str
    size_bytes: int


class SharedResourceSourceRecord(BaseModel):
    type: str
    id: str
    workspace_id: str
    materials: list[SharedResourceSourceMaterialRecord]


class SharedResourceFileRecord(BaseModel):
    path: str
    sha256: str
    size_bytes: int
    content_type: str


class SharedResourceVersionRecord(BaseModel):
    id: str
    resource_id: str
    version: int
    manifest_hash: str
    source: SharedResourceSourceRecord
    file_count: int
    total_size_bytes: int
    created_by: str
    created_at: str | None
    ready_at: str | None
    files: list[SharedResourceFileRecord] | None = None


class SharedResourceCurrentSelectionRecord(BaseModel):
    version_id: str
    version: int
    manifest_hash: str
    revision: int
    selected_by: str
    updated_at: str | None


class SharedResourceRecord(BaseModel):
    id: str
    name: str
    owner_workspace_id: str
    is_owner: bool
    access_status: str
    version_count: int
    current_selection: SharedResourceCurrentSelectionRecord | None
    created_by: str
    created_at: str | None


class SharedResourceGrantRecord(BaseModel):
    id: str
    resource_id: str
    workspace_id: str
    workspace_name: str
    status: str
    created_by: str
    created_at: str | None
    revoked_by: str
    revoked_at: str | None


class SharedResourceSelectionRecord(BaseModel):
    workspace_id: str
    resource_id: str
    version_id: str
    version: int
    manifest_hash: str
    revision: int
    selected_by: str
    updated_at: str | None


class SharedResourceResponse(BaseModel):
    resource: SharedResourceRecord


class SharedResourceListResponse(BaseModel):
    resources: list[SharedResourceRecord]
    total: int
    limit: int
    offset: int


class SharedResourceVersionResponse(BaseModel):
    version: SharedResourceVersionRecord


class SharedResourceVersionListResponse(BaseModel):
    versions: list[SharedResourceVersionRecord]


class SharedResourceShareResponse(BaseModel):
    grant: SharedResourceGrantRecord
    duplicate: bool


class SharedResourceGrantResponse(BaseModel):
    grant: SharedResourceGrantRecord


class SharedResourceGrantListResponse(BaseModel):
    grants: list[SharedResourceGrantRecord]


class SharedResourceSelectionResponse(BaseModel):
    selection: SharedResourceSelectionRecord


class SharedResourceDeleteVersionResponse(BaseModel):
    deleted: str
    version: int
    version_id: str
    physical_objects_deleted: bool
