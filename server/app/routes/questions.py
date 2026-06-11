from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from server.app.cms.client import get_token
from server.app.cms.question import fetch_question_detail
from server.app.jobs import JobQueries
from server.app.pipelines.resources import resolve_cms_resource
from server.app.settings import Settings

logger = logging.getLogger(__name__)


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

    @router.get(
        "/workspaces/{workspace_id}/questions/{question_id}", response_model=QuestionDetailResponse
    )
    def get_question_detail(workspace_id: str, question_id: str) -> QuestionDetailResponse:
        workspace = job_db.get_workspace(workspace_id)
        if workspace is None:
            raise HTTPException(status_code=404, detail="Workspace not found")

        cms_config = resolve_cms_resource(settings.config, workspace, None, "question_detail")
        api_url = cms_config.get("api_url") or cms_config.get("question_detail_url")
        title = question_id
        normalized: dict[str, Any] = {}
        cms_payload: dict[str, Any] | None = None

        if api_url:
            try:
                token = get_token(str(cms_config.get("env", "")), cms_config)
                detail = fetch_question_detail(question_id, str(api_url), token)
                title = detail.title or question_id
                normalized = detail.normalized
                cms_payload = detail.payload
            except Exception as exc:
                logger.warning("Failed to fetch question detail from CMS: %s", exc)
                raise HTTPException(
                    status_code=502, detail="Failed to fetch question from CMS"
                ) from exc
        else:
            # No CMS configured; return empty normalized so UI can still create jobs
            pass

        jobs = job_db.list_jobs(workspace_id=workspace_id, source_id=question_id)

        return QuestionDetailResponse(
            question_id=question_id,
            title=title,
            normalized=QuestionNormalized(**normalized),
            cms_payload=cms_payload,
            jobs=jobs,
        )

    return router
