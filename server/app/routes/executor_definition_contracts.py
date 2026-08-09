from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ExecutorDefinitionPayload(BaseModel):
    """Editable executor definition fields (raw executor config shape).

    Kept deliberately loose: the full typed parse (kind dispatch, path safety,
    config_schema contract) happens in ``ExecutorDefinitionService.save_draft``
    via ``load_executor_definitions``, which is the single validation source.
    """

    kind: str = Field(min_length=1)
    global_capacity: int = Field(ge=1)
    capabilities: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ExecutorCreateRequest(ExecutorDefinitionPayload):
    executor_id: str = Field(min_length=1)


class ExecutorCopyRequest(BaseModel):
    new_executor_id: str = Field(min_length=1)


class ExecutorRollbackRequest(BaseModel):
    version: int = Field(ge=1)


class ExecutorVersionResponse(BaseModel):
    id: str
    executor_id: str
    version: int
    status: Literal["draft", "published", "archived"]
    definition: dict[str, Any]
    definition_hash: str
    created_by: str
    created_at: datetime
    published_at: datetime | None = None


class ExecutorVersionSummary(BaseModel):
    id: str
    executor_id: str
    version: int
    status: Literal["draft", "published", "archived"]
    definition_hash: str
    created_by: str
    created_at: datetime
    published_at: datetime | None = None


class ExecutorListItem(BaseModel):
    executor_id: str
    kind: str
    global_capacity: int
    capabilities: list[str]
    version: int
    status: Literal["draft", "published", "archived"]
    has_draft: bool
    published_at: datetime | None = None


class ExecutorListResponse(BaseModel):
    executors: list[ExecutorListItem]


class ExecutorDetailResponse(BaseModel):
    executor_id: str
    latest: ExecutorVersionResponse | None = None
    published: ExecutorVersionResponse | None = None


class ExecutorVersionsResponse(BaseModel):
    versions: list[ExecutorVersionSummary]


class ExecutorArchiveResponse(BaseModel):
    archived: int
