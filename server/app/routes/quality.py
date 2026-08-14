from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import reject_studio_agent_scope, require_user
from server.app.routes.job_http import raise_job_http_error
from server.app.routes.quality_contracts import (
    QualityBatchStatsResponse,
    QualityLabelCreateRequest,
    QualityLabelResponse,
    QualitySampleBatchCreateRequest,
    QualitySampleBatchCreateResponse,
    QualitySampleBatchDetailResponse,
    QualitySampleBatchListResponse,
    QualitySampleItemDetailResponse,
    QualityStatsGroup,
)
from server.app.services.job_errors import JobServiceError
from server.app.services.quality_labels import QualityLabelService
from server.app.services.quality_sampling import QualitySamplingService
from server.app.services.quality_stats import QualityStatsService


def create_quality_router(
    sampling: QualitySamplingService,
    labels: QualityLabelService,
    stats: QualityStatsService,
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/workspaces/{workspace_id}/quality/sample-batches",
        response_model=QualitySampleBatchCreateResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def create_sample_batch(
        workspace_id: str,
        payload: QualitySampleBatchCreateRequest,
        user: Annotated[dict[str, Any], Depends(require_user)],
    ) -> QualitySampleBatchCreateResponse:
        try:
            result = sampling.create_batch(
                workspace_id,
                name=payload.name,
                workflow_key=payload.workflow_key or "",
                node_keys=payload.filters.node_keys,
                statuses=payload.filters.statuses,
                since=payload.filters.since,
                until=payload.filters.until,
                sample_size=payload.sample_size,
                seed=payload.seed,
                created_by=f"user:{user['id']}",
                filters=payload.filters.model_dump(mode="json", exclude_none=True),
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return QualitySampleBatchCreateResponse(**result)

    @router.get(
        "/workspaces/{workspace_id}/quality/sample-batches",
        response_model=QualitySampleBatchListResponse,
    )
    def list_sample_batches(workspace_id: str) -> QualitySampleBatchListResponse:
        try:
            batches = sampling.list_batches(workspace_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return QualitySampleBatchListResponse.model_validate({"batches": batches})

    @router.get(
        "/workspaces/{workspace_id}/quality/sample-batches/{batch_id}",
        response_model=QualitySampleBatchDetailResponse,
    )
    def get_sample_batch(
        workspace_id: str, batch_id: str, limit: int = 200, offset: int = 0
    ) -> QualitySampleBatchDetailResponse:
        try:
            batch = sampling.get_batch(workspace_id, batch_id)
            page = labels.list_batch_items(workspace_id, batch_id, limit=limit, offset=offset)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return QualitySampleBatchDetailResponse.model_validate(
            {"batch": batch, "items": page["items"], "total": page["total"]}
        )

    @router.get(
        "/workspaces/{workspace_id}/quality/sample-batches/{batch_id}/stats",
        response_model=QualityBatchStatsResponse,
    )
    def get_sample_batch_stats(workspace_id: str, batch_id: str) -> QualityBatchStatsResponse:
        try:
            groups = stats.batch_stats(workspace_id, batch_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return QualityBatchStatsResponse(
            batch_id=batch_id, groups=[QualityStatsGroup(**group) for group in groups]
        )

    @router.get(
        "/workspaces/{workspace_id}/quality/sample-items/{item_id}",
        response_model=QualitySampleItemDetailResponse,
    )
    def get_sample_item(workspace_id: str, item_id: str) -> QualitySampleItemDetailResponse:
        try:
            detail = labels.get_item_detail(workspace_id, item_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return QualitySampleItemDetailResponse.model_validate(detail)

    @router.post(
        "/workspaces/{workspace_id}/quality/sample-items/{item_id}/labels",
        response_model=QualityLabelResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def add_sample_item_label(
        workspace_id: str,
        item_id: str,
        payload: QualityLabelCreateRequest,
        user: Annotated[dict[str, Any], Depends(require_user)],
    ) -> QualityLabelResponse:
        try:
            label = labels.add_label(
                workspace_id,
                item_id,
                verdict=payload.verdict,
                reason_codes=payload.reason_codes,
                note=payload.note,
                labeled_by=f"user:{user['id']}",
                target="replay" if payload.replay_id else "run",
                replay_id=payload.replay_id,
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return QualityLabelResponse.model_validate({"label": label})

    return router
