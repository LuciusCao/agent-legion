"""Batch rerun preview contracts (read-only counts, no writes)."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, model_validator

from server.app.routes.job_batch_filter_contracts import JobSelectionMixin


class JobBatchRerunPreviewRequest(JobSelectionMixin):
    """Read-only count for a batch rerun selection (no writes).

    Exactly one mode: ``node_key`` (from-node rerun), ``from_failed_node``
    (failed-node rerun), or ``failure_category`` (category rerun).
    """

    node_key: str | None = None
    from_failed_node: bool = False
    failure_category: str | None = None

    @model_validator(mode="after")
    def check_preview_mode(self) -> Self:
        if self.failure_category is not None:
            if self.node_key is not None or self.from_failed_node:
                raise ValueError("failure_category cannot combine with node_key/from_failed_node")
        elif self.from_failed_node:
            if self.node_key is not None:
                raise ValueError("node_key must be None when from_failed_node is True")
        elif not self.node_key:
            raise ValueError("node_key is required when from_failed_node is False")
        return self


class BatchRerunPreviewResponse(BaseModel):
    total_count: int
    eligible_count: int
