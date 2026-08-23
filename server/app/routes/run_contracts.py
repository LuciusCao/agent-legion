"""Runs API contracts (materials-and-runs design §4/§5.2, slice 3)."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RunItemMaterial(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["material"]
    material_id: str = Field(min_length=1)


class RunItemRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ref"]
    connection_key: str = Field(min_length=1)
    external_id: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)


RunItem = Annotated[RunItemMaterial | RunItemRef, Field(discriminator="type")]


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_key: str = Field(min_length=1)
    items: list[RunItem] = Field(min_length=1)


class RunRecord(BaseModel):
    id: str
    workspace_id: str
    workflow_key: str
    source_kind: str
    status: str
    created_count: int
    error_message: str
    frozen_pins: dict[str, Any]
    stats: dict[str, Any]
    created_by: str
    created_at: str | None
    updated_at: str | None


class RunCreateResponse(BaseModel):
    run: RunRecord
    created_count: int
    jobs: list[dict[str, Any]]


class RunListResponse(BaseModel):
    runs: list[RunRecord]


class RunJobStats(BaseModel):
    total: int
    by_status: dict[str, int]


class RunDetailResponse(BaseModel):
    run: RunRecord
    job_stats: RunJobStats
