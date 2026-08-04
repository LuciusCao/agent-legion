from pydantic import BaseModel


class WorkspacePackageUpdate(BaseModel):
    name: str | None = None
    locked: bool | None = None


class WorkspacePackageItemResponse(BaseModel):
    id: int
    name: str
    path: str
    video_count: int
    size_bytes: int
    locked: int
    created_at: str
    workspace_id: str


class WorkspacePackagesResponse(BaseModel):
    packages: list[WorkspacePackageItemResponse]


class WorkspacePackageDeleteResponse(BaseModel):
    deleted: bool


class WorkspacePackageUpdateResponse(BaseModel):
    id: int
    name: str | None = None
    locked: bool | None = None
