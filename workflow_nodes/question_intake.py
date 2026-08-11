"""First node of question_comprehension_info: parse user input, fetch from CMS.

Intake fans out opaque candidates (``entity_id`` is the raw question id or
knowledge code); this node is the single place that talks to the CMS. The
intake mode is read from the frozen batch payload: ``knowledge_codes`` input
expands one job per code into a multi-question ``questions.json``; anything
else treats ``source_id`` as a single question id.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from server.app.cms import urls as cms_urls
from server.app.cms.client import get_token
from server.app.cms.question import fetch_question_detail, list_questions_by_knowledge
from server.app.executors.cancellation import check_cancellation

logger = logging.getLogger(__name__)


def _decode_json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _cms_config(context: dict[str, Any]) -> dict[str, Any]:
    """Effective CMS config: settings-level ``cms`` (env-injected) + node config.

    Node config carries the config_schema defaults (factory values) plus any
    node/workspace overrides and wins over the settings-level keys. The node
    config token arrives already resolved from the vault at dispatch
    time; it is a workspace-scoped credential, so mark it to win over the
    env-level global default (same precedence as the retired binding chain).
    """
    settings_config = context.get("settings_config")
    merged: dict[str, Any] = {}
    if isinstance(settings_config, dict):
        cms = settings_config.get("cms")
        if isinstance(cms, dict):
            merged = dict(cms)
    node_config = context.get("node_config")
    if isinstance(node_config, dict):
        merged.update({key: value for key, value in node_config.items() if value not in (None, "")})
    if merged.get("token"):
        merged["token_from_binding"] = True
    return merged


def _intake_input_field(job: dict[str, Any], context: dict[str, Any]) -> str:
    # The dispatch layer prefetches the batch row (custom sandboxed children
    # get no database handle, EXEC-CODE-003); the builtin child still carries
    # job_db and falls back to a live read.
    batch = context.get("job_batch")
    if not isinstance(batch, dict):
        job_db = context.get("job_db")
        if job_db is None:
            return ""
        batch = job_db.get_batch(str(job.get("batch_id", "")))
    if not batch:
        return ""
    payload = _decode_json_object(batch.get("source_payload_json"))
    mode = payload.get("intake_mode")
    if isinstance(mode, dict):
        return str(mode.get("input_field") or "")
    return ""


def _detail_payload(detail: Any, fallback_id: str, fallback_title: str) -> dict[str, Any]:
    return {
        "question_id": detail.question_id or fallback_id,
        "title": detail.title or fallback_title,
        "normalized": detail.normalized,
        "cms_payload": detail.payload,
    }


def _validate_fetched_detail(detail: Any, question_id: str) -> None:
    """Fail fast on CMS error payloads and empty stems.

    The CMS detail endpoint signals auth/parameter failures in-band via a
    non-zero ``code`` with ``data: null``; treating such payloads as valid
    questions poisons every downstream node (empty stem → empty key info →
    business validation failures). An error payload or a blank stem means the
    fetch did not produce a usable question, so the node must fail here as a
    technical failure instead of forwarding garbage.
    """
    payload = getattr(detail, "payload", None)
    if isinstance(payload, dict):
        code = payload.get("code")
        if code is not None and code != 0:
            message = payload.get("message") or ""
            raise RuntimeError(
                f"CMS 返回错误: code={code} message={message} (question_id={question_id})"
            )
    normalized = getattr(detail, "normalized", None)
    stem = normalized.get("stem") if isinstance(normalized, dict) else None
    if not str(stem or "").strip():
        raise RuntimeError(f"CMS 响应缺少题干 (question_id={question_id})")


def run(
    job: dict[str, Any],
    job_dir: Path,
    runtime: dict[str, Any] | None = None,
) -> None:
    context = runtime or {}
    check_cancellation(context)
    source_id = str(job["source_id"])
    knowledge_mode = _intake_input_field(job, context) == "knowledge_codes"
    logger.info(
        "question_intake: source_id=%s title=%s mode=%s",
        source_id,
        job.get("title", ""),
        "by_knowledge" if knowledge_mode else "by_id",
    )

    cms_config = _cms_config(context)
    node_config = context.get("node_config")
    if isinstance(node_config, dict) and node_config:
        logger.info("  node_config overrides: %s", sorted(node_config))

    questions: list[dict[str, Any]] = []
    if knowledge_mode:
        list_url = str(
            cms_config.get("question_list_url") or cms_urls.question_list_url(cms_config)
        )
        if not list_url:
            raise RuntimeError(
                f"knowledge mode requires a CMS question list URL (code={source_id})"
            )
        detail_url = str(
            cms_config.get("api_url")
            or cms_config.get("question_detail_url")
            or cms_urls.question_detail_url(cms_config)
        )
        token = get_token(str(cms_config.get("env", "")), cms_config)
        summaries = list_questions_by_knowledge(source_id, list_url, token)
        if not summaries:
            raise RuntimeError(f"no questions found for knowledge code: {source_id}")
        logger.info("  CMS returned %d question(s) for code=%s", len(summaries), source_id)
        for summary in summaries:
            check_cancellation(context)
            detail = fetch_question_detail(summary.question_id, detail_url, token)
            _validate_fetched_detail(detail, summary.question_id)
            questions.append(_detail_payload(detail, summary.question_id, summary.title))
    else:
        api_url = str(
            cms_config.get("api_url")
            or cms_config.get("question_detail_url")
            or cms_urls.question_detail_url(cms_config)
        )
        if api_url:
            logger.info("  fetching from CMS: %s", api_url)
            token = get_token(str(cms_config.get("env", "")), cms_config)
            detail = fetch_question_detail(source_id, str(api_url), token)
            check_cancellation(context)
            _validate_fetched_detail(detail, source_id)
            questions.append(_detail_payload(detail, source_id, str(job["title"])))
        else:
            logger.info("  no CMS configured, using base payload")
            questions.append(
                {
                    "question_id": source_id,
                    "title": job["title"],
                    "normalized": {},
                    "cms_payload": None,
                }
            )

    job_dir.mkdir(parents=True, exist_ok=True)
    out_path = job_dir / "questions.json"
    out_path.write_text(
        json.dumps({"questions": questions}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("  wrote %s (%d question(s))", out_path.name, len(questions))
