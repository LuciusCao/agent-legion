from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, model_validator

from server.app.routes.job_batch_filter_contracts import JobSelectionMixin


class JobMutationResultResponse(BaseModel):
    job_id: str
    operation: Literal["rerun", "run_to", "continue", "delete", "package", "upgrade_workflow"]
    status: Literal["succeeded", "skipped", "failed"]
    node_key: str | None = None
    reason_code: str | None = None
    message: str | None = None


class BatchJobMutationResponse(BaseModel):
    results: list[JobMutationResultResponse]


class JobBatchRerunRequest(JobSelectionMixin):
    node_key: str | None = None
    from_failed_node: bool = False

    @model_validator(mode="after")
    def check_node_key_or_from_failed(self) -> Self:
        if self.from_failed_node:
            if self.node_key is not None:
                raise ValueError("node_key must be None when from_failed_node is True")
        else:
            if not self.node_key:
                raise ValueError("node_key is required when from_failed_node is False")
        return self


# Deletion is a distinct mutation contract and may diverge in validation rules.
class BatchJobIdsRequest(JobSelectionMixin):
    pass


class RunToRequest(BaseModel):
    target_node_key: str
    start_node_key: str | None = None


class ContinueJobRequest(BaseModel):
    pass


class BatchRunToRequest(JobSelectionMixin):
    target_node_key: str
    start_node_key: str | None = None
