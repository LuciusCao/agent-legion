from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from server.app.cms.client import CmsClientError, get_token
from server.app.cms.question import fetch_question_detail
from server.app.jobs import JobQueries
from server.app.services.vault import VaultError, VaultService
from server.app.settings import Settings
from server.app.workflows.resources import resolve_cms_resource


class QuestionWorkspaceNotFoundError(LookupError):
    pass


class QuestionDetailFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class QuestionDetail:
    question_id: str
    title: str
    normalized: dict[str, Any]
    cms_payload: dict[str, Any] | None
    jobs: list[dict[str, Any]]


class QuestionDetailService:
    def __init__(self, job_db: JobQueries, settings: Settings) -> None:
        self._job_db = job_db
        self._settings = settings

    def get_detail(self, workspace_id: str, question_id: str) -> QuestionDetail:
        workspace = self._job_db.get_workspace(workspace_id)
        if workspace is None:
            raise QuestionWorkspaceNotFoundError(workspace_id)

        cms_config = resolve_cms_resource(self._settings.config, workspace, None, "question_detail")
        api_url = cms_config.get("api_url") or cms_config.get("question_detail_url")
        title = question_id
        normalized: dict[str, Any] = {}
        cms_payload: dict[str, Any] | None = None

        if api_url:
            try:
                # Resolve secret_ref markers in memory only; legacy plaintext
                # passes through (spec D14 compatibility window).
                cms_config = VaultService(
                    self._job_db.path, self._settings.config
                ).resolve_secret_refs(cms_config, workspace_id)
                token = get_token(str(cms_config.get("env", "")), cms_config)
                detail = fetch_question_detail(question_id, str(api_url), token)
            except (CmsClientError, VaultError) as exc:
                raise QuestionDetailFetchError(question_id) from exc
            title = detail.title or question_id
            normalized = detail.normalized
            cms_payload = detail.payload

        return QuestionDetail(
            question_id=question_id,
            title=title,
            normalized=normalized,
            cms_payload=cms_payload,
            jobs=self._job_db.list_jobs(workspace_id=workspace_id, source_id=question_id),
        )
