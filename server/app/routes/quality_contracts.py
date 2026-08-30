from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from server.app.services.quality_labels import QUALITY_REASON_CODES

LabelTarget = Literal["run", "replay"]
LabelVerdict = Literal["good", "bad"]


class QualitySampleFilters(BaseModel):
    node_keys: list[str] | None = None
    statuses: list[str] | None = None
    since: datetime | None = None
    until: datetime | None = None


class QualitySampleBatchCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # #211 Phase 2 second batch: optional with server-side default from the
    # path workspace_id (equal since schema v62); explicit values stay
    # accepted during the compatibility window.
    workflow_key: str | None = Field(
        min_length=1,
        default=None,
        description=(
            "Deprecated: defaults to the workspace id from the path (the two "
            "are equal since schema v62). Removal is tracked in #211."
        ),
        deprecated=True,
    )
    filters: QualitySampleFilters = Field(default_factory=QualitySampleFilters)
    sample_size: int = Field(gt=0, le=1000)
    seed: str | None = None


class QualitySampleBatch(BaseModel):
    id: str
    workspace_id: str
    name: str
    workflow_key: str
    filters: dict[str, Any] = Field(default_factory=dict, validation_alias="filters_json")
    sample_size: int
    seed: str
    created_by: str
    created_at: datetime


class QualitySampleBatchCreateResponse(QualitySampleBatch):
    sampled_count: int


class QualitySampleBatchListResponse(BaseModel):
    batches: list[QualitySampleBatch]


class QualityLabel(BaseModel):
    id: str
    item_id: str
    target: str
    verdict: str
    reason_codes: list[str] = Field(default_factory=list)
    note: str
    labeled_by: str
    replay_id: str | None = None
    created_at: datetime


class QualitySampleItem(BaseModel):
    id: str
    batch_id: str
    node_run_id: int
    job_id: str
    node_key: str
    capability: str
    skill_version: str
    agent_definition_hash: str
    agent_version: int | None = None
    provider: str
    model: str
    run_status: str
    failure_category: str
    failure_detail: str
    created_at: datetime
    current_label: QualityLabel | None = None


class QualitySampleBatchDetailResponse(BaseModel):
    batch: QualitySampleBatch
    items: list[QualitySampleItem]
    total: int


class QualityArtifactContent(BaseModel):
    name: str
    content: str
    truncated: bool = False


class QualitySampleItemDetailResponse(BaseModel):
    item: QualitySampleItem
    labels: list[QualityLabel]
    artifacts: list[QualityArtifactContent]


class QualityLabelCreateRequest(BaseModel):
    verdict: LabelVerdict
    reason_codes: list[str] = Field(default_factory=list)
    note: str = ""
    # Set to label a replay output (target='replay'); unset labels the run.
    replay_id: str | None = None

    @field_validator("reason_codes")
    @classmethod
    def _check_reason_codes(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - set(QUALITY_REASON_CODES))
        if unknown:
            raise ValueError(f"unknown reason codes: {', '.join(unknown)}")
        return value


class QualityLabelResponse(BaseModel):
    label: QualityLabel


class QualityReplayCreateRequest(BaseModel):
    # Unset replays with the currently published Agent version.
    agent_version: int | None = Field(default=None, gt=0)


class QualityReplay(BaseModel):
    id: str
    item_id: str
    agent_id: str = ""
    agent_version: int | None = None
    replay_job_id: str = ""
    status: str
    error_message: str = ""
    created_by: str = ""
    created_at: datetime
    finished_at: datetime | None = None


class QualityReplayResponse(BaseModel):
    replay: QualityReplay


class QualityReplayListResponse(BaseModel):
    replays: list[QualityReplay]


class QualityReplayDetailResponse(BaseModel):
    replay: QualityReplay
    labels: list[QualityLabel]
    artifacts: list[QualityArtifactContent]
    input_artifacts: list[QualityArtifactContent] = Field(default_factory=list)


class QualityConfusionMatrix(BaseModel):
    """Review outcome vs. human label. Positive class = 拦截 (review rejected).

    Cells: tp = 拦截+bad (正确拦截), fp = 拦截+good (误杀),
    fn = 放行+bad (漏放), tn = 放行+good (正确放行).
    precision = tp/(tp+fp) — 拦截中确实 bad 的比例;
    recall = tp/(tp+fn) — bad 中被拦住的比例; both None when undefined.
    accuracy = (tp+tn)/total over labeled, classifiable items.
    """

    tp: int
    fp: int
    fn: int
    tn: int
    precision: float | None = None
    recall: float | None = None
    accuracy: float


class QualityStatsGroup(BaseModel):
    node_key: str
    skill_version: str
    provider: str
    model: str
    runs: int
    succeeded: int
    success_rate: float
    labeled: int
    good: int
    bad: int
    good_rate: float | None = None
    # None when the group has no labeled item with a classifiable review
    # outcome (completed = 放行, failed + 'review_rejected' = 拦截).
    confusion_matrix: QualityConfusionMatrix | None = None


class QualityBatchStatsResponse(BaseModel):
    batch_id: str
    groups: list[QualityStatsGroup]
