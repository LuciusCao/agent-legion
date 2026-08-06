"""Quality replay routes (schema v29): create/list/inspect replays of a
sampled node run. Replay labels reuse the sample-item labels endpoint with a
``replay_id`` (see ``quality.py``)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import require_user
from server.app.routes.job_http import raise_job_http_error
from server.app.routes.quality_contracts import (
    QualityReplayCreateRequest,
    QualityReplayDetailResponse,
    QualityReplayListResponse,
    QualityReplayResponse,
)
from server.app.services.job_errors import JobServiceError
from server.app.services.quality_replays import QualityReplayService


def create_quality_replays_router(replays: QualityReplayService) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/workspaces/{workspace_id}/quality/sample-items/{item_id}/replays",
        response_model=QualityReplayResponse,
    )
    def create_replay(
        workspace_id: str,
        item_id: str,
        payload: QualityReplayCreateRequest,
        user: Annotated[dict[str, Any], Depends(require_user)],
    ) -> QualityReplayResponse:
        try:
            replay = replays.create_replay(
                workspace_id,
                item_id,
                agent_version=payload.agent_version,
                created_by=f"user:{user['id']}",
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return QualityReplayResponse.model_validate({"replay": replay})

    @router.get(
        "/workspaces/{workspace_id}/quality/sample-items/{item_id}/replays",
        response_model=QualityReplayListResponse,
    )
    def list_replays(workspace_id: str, item_id: str) -> QualityReplayListResponse:
        try:
            rows = replays.list_replays(workspace_id, item_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return QualityReplayListResponse.model_validate({"replays": rows})

    @router.get(
        "/workspaces/{workspace_id}/quality/replays/{replay_id}",
        response_model=QualityReplayDetailResponse,
    )
    def get_replay(workspace_id: str, replay_id: str) -> QualityReplayDetailResponse:
        try:
            detail = replays.get_replay_detail(workspace_id, replay_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return QualityReplayDetailResponse.model_validate(detail)

    return router
