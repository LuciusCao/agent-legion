"""Runs API contracts (materials-and-runs design §4/§5.2, slice 3)."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# #211 Phase 2: request-param deprecation wording (server-side default).
_DEPRECATED_DEFAULT = (
    "Deprecated: defaults to the path workspace_id; removal tracked in #211 (drops by 2026-10-31)."
)
_DEPRECATED_READ = "Deprecated: read workspace_id instead. Since schema v62 the two are equal; removal tracked in #211 (drops by 2026-10-31)."

# Field-level length ceilings (PR #541 round-3 P1): item IDs resolve against
# materials/bundles/connections whose own identifiers are bounded well below
# this, so a megabyte-scale "id" can only be a mistake or an attack — and a
# single-item request carrying one would otherwise reach the service before
# any byte bound (the item-count ceiling cannot see it). 512 leaves generous
# room for every legitimate id shape. params is a free-form dispatch payload
# (the per-item job input); 64 KiB serialized keeps one item's input in the
# same order of magnitude as the node-config payloads it feeds, far below the
# 50 MB manifest ceiling, so per-item cost stays bounded by count.
MAX_ITEM_ID_LENGTH = 512
MAX_ITEM_PARAMS_BYTES = 65_536


class RunItemMaterial(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["material"]
    material_id: str = Field(min_length=1, max_length=MAX_ITEM_ID_LENGTH)


class RunItemRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ref"]
    connection_key: str = Field(min_length=1, max_length=MAX_ITEM_ID_LENGTH)
    external_id: str = Field(min_length=1, max_length=MAX_ITEM_ID_LENGTH)
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_params_size(self) -> Self:
        # Bounding the serialized form, not the Python size: json.dumps is
        # the canonical wire form the manifest stores and re-reads.
        size = len(json.dumps(self.params, ensure_ascii=False, default=str))
        if size > MAX_ITEM_PARAMS_BYTES:
            raise ValueError(
                f"params is {size} bytes serialized, exceeding the"
                f" {MAX_ITEM_PARAMS_BYTES} byte per-item limit"
            )
        return self


class RunItemBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["bundle"]
    bundle_id: str = Field(min_length=1, max_length=MAX_ITEM_ID_LENGTH)


RunItem = Annotated[RunItemMaterial | RunItemRef | RunItemBundle, Field(discriminator="type")]


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # #211 Phase 2: absent defaults to the path workspace_id (equal since v62).
    workflow_key: str | None = Field(
        default=None, min_length=1, deprecated=True, description=_DEPRECATED_DEFAULT
    )
    items: list[RunItem] = Field(min_length=1)


class RunRecord(BaseModel):
    id: str
    workspace_id: str
    workflow_key: str = Field(description=_DEPRECATED_READ, deprecated=True)
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
    """#467 A4：run + created_count only；job 行移到读取路径（#420）。"""

    run: RunRecord
    created_count: int


class RunListResponse(BaseModel):
    runs: list[RunRecord]


class RunJobStats(BaseModel):
    total: int
    by_status: dict[str, int]


class RunDetailResponse(BaseModel):
    run: RunRecord
    job_stats: RunJobStats
