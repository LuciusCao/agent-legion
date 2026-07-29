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

from server.app.cms.client import get_token
from server.app.cms.question import fetch_question_detail, list_questions_by_knowledge
from server.app.executors.cancellation import check_cancellation
from server.app.workflows.cms_helpers import _decode_json_object, _effective_cms_config

logger = logging.getLogger(__name__)


def _intake_input_field(job: dict[str, Any], context: dict[str, Any]) -> str:
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

    cms_config = _effective_cms_config(
        job, context, resource_key="by_knowledge" if knowledge_mode else "question_detail"
    )
    node_config = context.get("node_config")
    if isinstance(node_config, dict) and node_config:
        logger.info("  node_config overrides: %s", sorted(node_config))

    questions: list[dict[str, Any]] = []
    if knowledge_mode:
        list_url = str(cms_config.get("api_url") or cms_config.get("question_list_url") or "")
        if not list_url:
            raise RuntimeError(
                f"knowledge mode requires a CMS question list URL (code={source_id})"
            )
        detail_url = str(cms_config.get("question_detail_url") or cms_config.get("api_url") or "")
        token = get_token(str(cms_config.get("env", "")), cms_config)
        summaries = list_questions_by_knowledge(source_id, list_url, token)
        if not summaries:
            raise RuntimeError(f"no questions found for knowledge code: {source_id}")
        logger.info("  CMS returned %d question(s) for code=%s", len(summaries), source_id)
        for summary in summaries:
            check_cancellation(context)
            detail = fetch_question_detail(summary.question_id, detail_url, token)
            questions.append(_detail_payload(detail, summary.question_id, summary.title))
    else:
        api_url = cms_config.get("api_url") or cms_config.get("question_detail_url")
        if api_url:
            logger.info("  fetching from CMS: %s", api_url)
            token = get_token(str(cms_config.get("env", "")), cms_config)
            detail = fetch_question_detail(source_id, str(api_url), token)
            check_cancellation(context)
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
