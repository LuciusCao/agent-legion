"""Shared filter payload for workspace job batch selections."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, Field, model_validator

from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.services.job_selection_resolver import MAX_BATCH_JOBS


class JobFilterPayload(BaseModel):
    """Job list filter embedded in batch requests; mirrors the list query params."""

    status: str | None = None
    search: str | None = None
    workflow_version: int | None = None
    workflow_version_none: bool = False
    active_node_key: str | None = None
    packed: int | None = None
    paused: bool | None = None

    def to_filter(self) -> JobListFilter:
        return JobListFilter(
            status=self.status,
            search=self.search,
            workflow_version=self.workflow_version,
            workflow_version_none=self.workflow_version_none,
            active_node_key=self.active_node_key,
            packed=self.packed,
            paused=self.paused,
        )


class JobSelectionMixin(BaseModel):
    """Batch target selection: exactly one of explicit ``job_ids`` or ``filter``."""

    # #712: explicit lists are bounded by the same per-request cap as filter
    # matches (``MAX_BATCH_JOBS``, enforced in the selection resolver);
    # oversized lists fail validation with 422 before any work starts.
    job_ids: list[str] | None = Field(default=None, max_length=MAX_BATCH_JOBS)
    filter: JobFilterPayload | None = None
    exclude_ids: list[str] = Field(default_factory=list, max_length=MAX_BATCH_JOBS)

    @model_validator(mode="after")
    def check_job_selection(self) -> Self:
        if (self.job_ids is None) == (self.filter is None):
            raise ValueError("Provide exactly one of job_ids or filter")
        return self

    def resolved_filter(self) -> JobListFilter | None:
        return self.filter.to_filter() if self.filter is not None else None
