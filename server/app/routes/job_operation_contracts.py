from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from server.app.routes.job_batch_filter_contracts import JobSelectionMixin

#: upgrade-workflow 的重置模式（issue #645）：clean = 全量重跑（既有
#: 行为，默认）；inherit = 未变节点保持 completed 继承产物，仅变更
#: 子图重跑。
UpgradeMode = Literal["clean", "inherit"]


class JobMutationResultResponse(BaseModel):
    job_id: str
    operation: Literal[
        "rerun", "run_to", "continue", "delete", "package", "upgrade_workflow", "pause", "resume"
    ]
    status: Literal["succeeded", "skipped", "failed"]
    node_key: str | None = None
    reason_code: str | None = None
    message: str | None = None
    # upgrade_workflow 专属统计（issue #645）：其余 operation 不携带；
    # Optional + default 让 OpenAPI 输出为可省略字段（旧客户端与非
    # upgrade 结果的既有 fixture 不必补齐）。
    mode: UpgradeMode | None = None
    kept_nodes: int | None = Field(default=None, ge=0)
    rerun_nodes: int | None = Field(default=None, ge=0)


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


class BatchPauseJobsRequest(JobSelectionMixin):
    reason: str | None = None


class BatchResumeJobsRequest(JobSelectionMixin):
    pass


class BatchUpgradeWorkflowRequest(JobSelectionMixin):
    mode: UpgradeMode = "clean"


class UpgradeWorkflowRequest(BaseModel):
    mode: UpgradeMode = "clean"


class RunToRequest(BaseModel):
    target_node_key: str
    start_node_key: str | None = None


class ContinueJobRequest(BaseModel):
    pass


class BatchRunToRequest(JobSelectionMixin):
    target_node_key: str
    start_node_key: str | None = None
