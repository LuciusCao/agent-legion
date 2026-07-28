from __future__ import annotations

import logging
from typing import Any

from server.app.cms.client import get_token
from server.app.cms.question import fetch_question_detail, list_questions_by_knowledge
from server.app.services.job_errors import InvalidOperationError, UnsupportedOperationError
from server.app.services.vault import VaultError, VaultService
from server.app.settings import Settings
from server.app.workflows.resources import resolve_cms_resource

logger = logging.getLogger(__name__)

RESOLVER_MAP: dict[tuple[str, str], str] = {
    ("question", "direct_ids"): "direct.question_ids",
    ("question", "by_knowledge"): "cms.questions_by_knowledge",
    ("question", "batch_by_ids"): "cms.question_ids",
    ("question", "batch_by_knowledge"): "cms.questions_by_knowledge",
    ("video", "direct_ids"): "direct.video_ids",
    ("video", "by_knowledge"): "cms.videos_by_knowledge",
    ("video", "batch_by_urls"): "direct.video_urls",
    ("video", "batch_by_knowledge"): "cms.knowledge_video",
}


def candidate(
    entity_type: str,
    entity_id: str,
    title: str,
    source_kind: str,
    source_value: str,
    stem: str = "",
    **extras: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "title": title,
        "stem": stem,
        "source": {"kind": source_kind, "value": source_value},
    }
    result.update(extras)
    return result


def normalize_values(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def resolve_direct_candidates(
    entity: str,
    input_values: list[str],
    source_kind: str,
) -> list[dict[str, Any]]:
    if entity == "video" and source_kind == "batch_by_urls":
        from server.app.services.job_intake_video import resolve_video_url_candidates

        return resolve_video_url_candidates(input_values, source_kind)
    return [
        candidate(entity, value, f"{entity.title()} {value}", source_kind, value)
        for value in input_values
    ]


def resolve_cms_question_candidates(
    entity: str,
    input_values: list[str],
    source_kind: str,
    resolver: str,
    mode: Any,
    settings: Settings,
    workspace: dict[str, Any],
    workspace_id: str,
) -> list[dict[str, Any]]:
    if entity != "question":
        raise UnsupportedOperationError(f"{entity} resolver not yet implemented")

    resource_key = "question_detail" if resolver == "cms.question_ids" else "by_knowledge"

    cms_resource = resolve_cms_resource(
        settings.config,
        workspace,
        None,
        resource_key,
        declarations=settings.resource_providers,
    )
    try:
        # Resolve secret_ref markers in memory only; legacy plaintext passes
        # through (spec D14 compatibility window).
        cms_resource = VaultService(settings.database_url, settings.config).resolve_secret_refs(
            cms_resource, workspace_id
        )
    except VaultError as exc:
        raise InvalidOperationError(str(exc)) from exc
    api_url = cms_resource.get("api_url") or cms_resource.get(
        "question_list_url" if resolver == "cms.questions_by_knowledge" else "question_detail_url"
    )
    logger.info(
        "CMS lookup for workspace=%s mode=%s: api_url=%s resource=%s",
        workspace_id,
        mode.key,
        api_url,
        resource_key,
    )
    token = get_token(str(cms_resource.get("env", "")), cms_resource)

    candidates: list[dict[str, Any]] = []
    seen_question_ids: set[str] = set()

    if resolver == "cms.question_ids":
        for question_id in input_values:
            question_detail = fetch_question_detail(question_id, api_url, token)
            logger.info(
                "CMS lookup for question_id=%s title=%s",
                question_id,
                question_detail.title,
            )
            if question_detail.question_id in seen_question_ids:
                continue
            seen_question_ids.add(question_detail.question_id)
            candidates.append(
                candidate(
                    entity,
                    question_detail.question_id,
                    question_detail.title or f"Question {question_detail.question_id}",
                    source_kind,
                    question_id,
                    stem=question_detail.normalized.get("stem", ""),
                )
            )
    else:
        for knowledge_code in input_values:
            summaries = list_questions_by_knowledge(knowledge_code, api_url, token)
            logger.info(
                "CMS returned %d questions for knowledge_code=%s",
                len(summaries),
                knowledge_code,
            )
            for summary in summaries:
                if summary.question_id in seen_question_ids:
                    continue
                seen_question_ids.add(summary.question_id)
                stem = ""
                body = summary.payload.get("body")
                if isinstance(body, dict):
                    stem = str(body.get("content") or "").strip()
                candidates.append(
                    candidate(
                        entity,
                        summary.question_id,
                        summary.title or f"Question {summary.question_id}",
                        "knowledge_code",
                        knowledge_code,
                        stem=stem,
                    )
                )

    return candidates
