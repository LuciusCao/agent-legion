"""Campaigns API contracts (#532 PR-A, design §3.2).

Pydantic mirrors of the campaign surface. The rerun/upgrade target reuses
the shared batch filter payload (JobFilterPayload) so campaign filters and
job-list filters cannot drift; submit targets are the POST /runs RunItem
union. Create accepts JSON (inline items) or multipart (manifest file) —
FastAPI cannot express both on one path in a single model, so the multipart
variant parses its fields by hand in the route (campaigns.py) and feeds the
same service entry point.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from server.app.routes.job_batch_filter_contracts import JobFilterPayload
from server.app.routes.run_contracts import RunItem

CampaignMode = Literal["rerun", "submit", "upgrade"]
CampaignStatus = Literal["pending", "running", "paused", "failed", "completed", "cancelled"]

# Inline items count ceiling (PR #541 round-2 P1): FastAPI fully parses the
# JSON body into RunItem models BEFORE any service check runs, so a
# multi-copy-of-the-byte-limit body would balloon memory ahead of the
# manifest_max_bytes 413. The count bound makes the inline channel bounded
# at the contract layer instead — the model list construction is refused at
# 5×10^5 items (~10^3 MB even at a minimal item, comfortably beyond the
# 50 MB byte ceiling the service enforces on the serialized form; the
# multipart channel is already size-bounded at read time). Numbers are
# matched, not derived: the two ceilings bound the same product boundary.
MAX_MANIFEST_ITEMS = 500_000


class CampaignKnobsMixin(BaseModel):
    """Per-campaign overrides of the instance defaults (design §2.5)."""

    watermark: int | None = Field(default=None, ge=1)
    batch_size: int | None = Field(default=None, ge=1)


class CampaignRerunTarget(CampaignKnobsMixin):
    """rerun/upgrade: exactly one of job_ids or filter, plus rerun knobs.

    The node_key/from_failed_node pair follows JobBatchRerunRequest's rule
    but only for rerun mode (upgrade re-pins the revision and reruns from
    the top — there is no node selection to make); the cross-mode rule is
    checked in CampaignCreateRequest where the mode is known.
    """

    node_key: str | None = None
    from_failed_node: bool = False
    job_ids: list[str] | None = None
    filter: JobFilterPayload | None = None

    @model_validator(mode="after")
    def check_selection(self) -> Self:
        if (self.job_ids is None) == (self.filter is None):
            raise ValueError("Provide exactly one of job_ids or filter")
        return self

    def check_rerun_node_selection(self) -> None:
        """node_key / from_failed_node rule (rerun mode only)."""
        if self.from_failed_node:
            if self.node_key is not None:
                raise ValueError("node_key must be None when from_failed_node is True")
        elif not self.node_key:
            raise ValueError("node_key is required when from_failed_node is False")

    def resolved_filter(self) -> Any:

        return self.filter.to_filter() if self.filter is not None else None


class CampaignSubmitInlineTarget(CampaignKnobsMixin):
    """submit, inline channel: a bounded items array in the JSON body."""

    # max_length bounds the body BEFORE Pydantic builds the model list (the
    # round-2 P1 read-then-limit fix): an oversized array fails request
    # validation with a plain 422 instead of deserializing half a million
    # models first.
    items: list[RunItem] = Field(min_length=1, max_length=MAX_MANIFEST_ITEMS)


class CampaignCreateRequest(BaseModel):
    """JSON create body; the multipart variant lives in the route."""

    model_config = ConfigDict(extra="forbid")

    mode: CampaignMode
    rerun: CampaignRerunTarget | None = None
    submit: CampaignSubmitInlineTarget | None = None

    @model_validator(mode="after")
    def check_target(self) -> Self:
        targets = [target for target in (self.rerun, self.submit) if target is not None]
        if len(targets) != 1:
            raise ValueError("Provide exactly one target block (rerun or submit)")
        if self.rerun is not None and self.mode == "submit":
            raise ValueError("mode=submit requires the submit target block")
        if self.submit is not None and self.mode != "submit":
            raise ValueError("the submit target block requires mode=submit")
        if self.rerun is not None and self.mode == "rerun":
            self.rerun.check_rerun_node_selection()
        return self


class CampaignPreviewRequest(CampaignCreateRequest):
    """Dry-run the creation judgements; same shape, no writes."""


class CampaignRecord(BaseModel):
    id: str
    workspace_id: str
    mode: CampaignMode
    status: CampaignStatus
    target_spec: dict[str, Any]
    progress: dict[str, Any]
    watermark: int
    batch_size: int
    batches_submitted: int
    jobs_succeeded: int
    jobs_skipped: int
    jobs_failed: int
    error_message: str
    created_by: str
    created_at: str | None
    updated_at: str | None
    finished_at: str | None


class CampaignCreateResponse(BaseModel):
    campaign: CampaignRecord


class CampaignListResponse(BaseModel):
    campaigns: list[CampaignRecord]


class CampaignDetailResponse(BaseModel):
    campaign: CampaignRecord


class CampaignRerunPreviewResult(BaseModel):
    mode: Literal["rerun", "upgrade"]
    total_count: int
    eligible_count: int
    estimated_batches: int
    batch_size: int


class CampaignSubmitPreviewResult(BaseModel):
    mode: Literal["submit"]
    total_items: int
    would_create: int
    would_skip: int
    estimated_batches: int
    batch_size: int


class CampaignPreviewResponse(BaseModel):
    """Discriminated union of the per-mode preview results (named model so the
    OpenAPI response schema is a $ref, not an inline blob)."""

    result: Annotated[
        CampaignRerunPreviewResult | CampaignSubmitPreviewResult,
        Field(discriminator="mode"),
    ]


class CampaignStatusChangeResponse(BaseModel):
    campaign: CampaignRecord
