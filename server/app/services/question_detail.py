from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from server.app.jobs import JobQueries
from server.app.services.connection_tokens import ConnectionTokenService
from server.app.services.job_errors import JobServiceError, NotFoundError
from server.app.services.node_config import workspace_node_overrides
from server.app.services.node_connection import workspace_node_connection_key
from server.app.services.vault import VaultError
from server.app.settings import Settings
from workspace_libs.cms import urls as cms_urls
from workspace_libs.cms.client import CmsClientError
from workspace_libs.cms.question import fetch_question_detail


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

        connection_key = workspace_node_connection_key(
            self._settings.executor_definitions,
            workspace,
            "question_comprehension_info",
            "fetch_questions",
            "fetch_questions",
        )
        title = question_id
        normalized: dict[str, Any] = {}
        cms_payload: dict[str, Any] | None = None

        if connection_key:
            try:
                tokens = ConnectionTokenService(self._job_db.path, self._settings.config)
                cms_config = tokens.runtime_config(connection_key)
            except NotFoundError:
                # No connection on this instance (fresh/no-CMS deployment):
                # degrade to local data instead of failing the page.
                cms_config = None
            except (VaultError, JobServiceError) as exc:
                raise QuestionDetailFetchError(question_id) from exc
            if cms_config is not None:
                try:
                    # Workspace business overrides (bank_version etc.) win.
                    override = workspace_node_overrides(
                        workspace, "question_comprehension_info"
                    ).get("fetch_questions", {})
                    cms_config.update(
                        {
                            key: value
                            for key, value in override.items()
                            if key not in ("connection", "token") and value not in (None, "")
                        }
                    )
                    api_url = str(
                        cms_config.get("api_url") or cms_urls.question_detail_url(cms_config)
                    )
                    # Connection without base_url/api_url (e.g. a migrated
                    # token-only connection): degrade to local data instead
                    # of failing the page.
                    if api_url:
                        detail = fetch_question_detail(
                            question_id, api_url, cms_config.get("token")
                        )
                        title = detail.title or question_id
                        normalized = detail.normalized
                        cms_payload = detail.payload
                except CmsClientError as exc:
                    raise QuestionDetailFetchError(question_id) from exc

        return QuestionDetail(
            question_id=question_id,
            title=title,
            normalized=normalized,
            cms_payload=cms_payload,
            jobs=self._job_db.list_jobs(workspace_id=workspace_id, source_id=question_id),
        )
