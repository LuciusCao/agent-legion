from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from server.app.cms.client import get_token
from server.app.cms.question import fetch_question_detail
from server.app.executors.cancellation import check_cancellation
from server.app.pipelines.question_content import _effective_cms_config


def _first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _nested_dict(value: Any, key: str) -> dict[str, Any]:
    if isinstance(value, dict) and isinstance(value.get(key), dict):
        return cast(dict[str, Any], value[key])
    return {}


def _extract_cms_fingerprint(question: dict[str, Any]) -> str | None:
    normalized = question.get("normalized")
    cms_payload = question.get("cms_payload")
    data = _nested_dict(cms_payload, "data")
    return _first_string(
        normalized.get("fingerprint") if isinstance(normalized, dict) else None,
        data.get("fingerprint"),
        data.get("question_fingerprint"),
        data.get("content_fingerprint"),
    )


def fetch_questions(
    job: dict[str, Any],
    artifact_dir: Path,
    context: dict[str, Any] | None = None,
) -> None:
    context = context or {}
    check_cancellation(context)

    cms_config = _effective_cms_config(job, context)
    api_url = cms_config.get("api_url") or cms_config.get("question_detail_url")
    if api_url:
        token = get_token(str(cms_config.get("env", "")), cms_config)
        detail = fetch_question_detail(str(job["source_id"]), str(api_url), token)
        check_cancellation(context)
        payload = {
            "question_id": detail.question_id or job["source_id"],
            "title": detail.title or job["title"],
            "normalized": detail.normalized,
            "cms_payload": detail.payload,
        }
    else:
        payload = {
            "question_id": job["source_id"],
            "title": job["title"],
            "normalized": {},
            "cms_payload": None,
        }

    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "questions.json").write_text(
        json.dumps({"questions": [payload]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clean_and_parse(
    job: dict[str, Any],
    artifact_dir: Path,
    context: dict[str, Any] | None = None,
) -> None:
    questions_path = artifact_dir / "questions.json"
    if not questions_path.is_file():
        raise ValueError("questions.json not found")

    check_cancellation(context)
    data = json.loads(questions_path.read_text(encoding="utf-8"))
    questions = data.get("questions", [])
    if not isinstance(questions, list) or not questions:
        raise ValueError("questions.json contains no questions")

    parsed_questions: list[dict[str, Any]] = []
    for q in questions:
        check_cancellation(context)
        if not isinstance(q, dict):
            raise ValueError("Invalid question record in questions.json")
        qid = q.get("question_id")
        if not qid:
            raise ValueError("Missing question_id in questions.json")
        normalized = q.get("normalized") or {}
        if not isinstance(normalized, dict):
            normalized = {}
        fingerprint = _extract_cms_fingerprint(q)
        parsed_questions.append(
            {
                "question_id": str(qid),
                "stem": normalized.get("stem") or "",
                "options": normalized.get("options") or [],
                "answer": normalized.get("answer") or "",
                "analysis": normalized.get("analysis") or "",
                "fingerprint": fingerprint,
                "fingerprint_source": "cms" if fingerprint else "missing",
                "fingerprint_missing": fingerprint is None,
            }
        )

    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "questions_parsed.json").write_text(
        json.dumps({"questions": parsed_questions}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
