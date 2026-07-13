from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from server.app.jobs import JobQueries
from server.app.services.question_detail import (
    QuestionDetailFetchError,
    QuestionDetailService,
    QuestionWorkspaceNotFoundError,
)
from server.app.settings import Settings


class QuestionNormalized(BaseModel):
    stem: str | None = None
    options: list[dict[str, Any]] | None = None
    answer: Any | None = None
    analysis: Any | None = None
    answer_blanks: list[dict[str, Any]] | None = None
    analysis_steps: list[list[dict[str, Any]]] | None = None


class QuestionDetailResponse(BaseModel):
    question_id: str
    title: str
    normalized: QuestionNormalized
    cms_payload: dict[str, Any] | None = None
    jobs: list[dict[str, Any]]


def create_questions_router(job_db: JobQueries, settings: Settings) -> APIRouter:
    router = APIRouter()
    service = QuestionDetailService(job_db, settings)

    @router.get(
        "/workspaces/{workspace_id}/questions/{question_id}", response_model=QuestionDetailResponse
    )
    def get_question_detail(workspace_id: str, question_id: str) -> QuestionDetailResponse:
        try:
            detail = service.get_detail(workspace_id, question_id)
        except QuestionWorkspaceNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Workspace not found") from exc
        except QuestionDetailFetchError as exc:
            raise HTTPException(
                status_code=502, detail="Failed to fetch question from CMS"
            ) from exc

        return QuestionDetailResponse(
            question_id=detail.question_id,
            title=detail.title,
            normalized=QuestionNormalized(**detail.normalized),
            cms_payload=detail.cms_payload,
            jobs=detail.jobs,
        )

    return router
