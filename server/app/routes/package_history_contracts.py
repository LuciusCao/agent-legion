from pydantic import BaseModel


class PackageUpdate(BaseModel):
    name: str | None = None
    locked: bool | None = None


class WorkspacePackageUpdate(BaseModel):
    name: str | None = None
    locked: bool | None = None


class PackageItemResponse(BaseModel):
    id: int
    name: str
    path: str
    video_count: int
    size_bytes: int
    locked: int
    created_at: str


class PackagesResponse(BaseModel):
    packages: list[PackageItemResponse]


class WorkspacePackageItemResponse(PackageItemResponse):
    workspace_id: str


class WorkspacePackagesResponse(BaseModel):
    packages: list[WorkspacePackageItemResponse]


class PackageDeleteResponse(BaseModel):
    deleted: bool


class PackageUpdateResponse(BaseModel):
    id: int
    name: str | None = None
    locked: bool | None = None


class WorkspacePackageDeleteResponse(BaseModel):
    deleted: bool


class WorkspacePackageUpdateResponse(BaseModel):
    id: int
    name: str | None = None
    locked: bool | None = None
