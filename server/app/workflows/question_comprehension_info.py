from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from server.app.cms.client import get_token
from server.app.cms.question import fetch_question_detail
from server.app.executors.cancellation import check_cancellation
from server.app.workflows.cms_helpers import _effective_cms_config
from server.app.workflows.question_fingerprint import (
    compute_question_fingerprint,
    extract_cms_fingerprint,
)
from server.app.workflows.skill_version_collection import collect_skill_versions

logger = logging.getLogger(__name__)


def fetch_questions(
    job: dict[str, Any],
    artifact_dir: Path,
    context: dict[str, Any] | None = None,
) -> None:
    context = context or {}
    check_cancellation(context)
    logger.info(
        "fetch_questions: source_id=%s title=%s",
        job["source_id"],
        job.get("title", ""),
    )

    cms_config = _effective_cms_config(job, context)
    api_url = cms_config.get("api_url") or cms_config.get("question_detail_url")
    if api_url:
        logger.info("  fetching from CMS: %s", api_url)
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
        logger.info("  no CMS configured, using base payload")
        payload = {
            "question_id": job["source_id"],
            "title": job["title"],
            "normalized": {},
            "cms_payload": None,
        }

    artifact_dir.mkdir(parents=True, exist_ok=True)
    out_path = artifact_dir / "questions.json"
    out_path.write_text(
        json.dumps({"questions": [payload]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("  wrote %s", out_path.name)


def clean_and_parse(
    job: dict[str, Any],
    artifact_dir: Path,
    context: dict[str, Any] | None = None,
) -> None:
    context = context or {}
    questions_path = artifact_dir / "questions.json"
    if not questions_path.is_file():
        raise ValueError("questions.json not found")

    check_cancellation(context)
    logger.info("clean_and_parse: source_id=%s", job["source_id"])
    data = json.loads(questions_path.read_text(encoding="utf-8"))
    questions = data.get("questions", [])
    if not isinstance(questions, list) or not questions:
        raise ValueError("questions.json contains no questions")

    logger.info("  parsing %d question(s)", len(questions))
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
        fingerprint = extract_cms_fingerprint(q)
        source = "cms"
        if fingerprint is None:
            fingerprint = compute_question_fingerprint(
                normalized.get("stem") or "",
                normalized.get("options") or [],
            )
            source = "md5" if fingerprint is not None else "missing"
        parsed_questions.append(
            {
                "question_id": str(qid),
                "stem": normalized.get("stem") or "",
                "options": normalized.get("options") or [],
                "answer": normalized.get("answer") or "",
                "analysis": normalized.get("analysis") or "",
                "fingerprint": fingerprint,
                "fingerprint_source": source,
                "fingerprint_missing": fingerprint is None,
            }
        )
        logger.info(
            "    parsed question_id=%s fingerprint=%s source=%s",
            qid,
            "present" if fingerprint else "missing",
            source,
        )

    artifact_dir.mkdir(parents=True, exist_ok=True)
    out_path = artifact_dir / "questions_parsed.json"
    out_path.write_text(
        json.dumps({"questions": parsed_questions}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("  wrote %s", out_path.name)


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"Missing input: {path.name}")
    content = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(content, dict):
        raise ValueError(f"Invalid content in {path.name}")
    return content


def _single_parsed_question(artifact_dir: Path, source_id: str) -> dict[str, Any]:
    parsed = _load_json_object(artifact_dir / "questions_parsed.json")
    questions = parsed.get("questions")
    if not isinstance(questions, list) or len(questions) != 1:
        raise ValueError("questions_parsed.json must contain exactly one question")
    question = questions[0]
    if not isinstance(question, dict):
        raise ValueError("questions_parsed.json contains an invalid question")
    if question.get("question_id") != source_id:
        raise ValueError(f"Expected question_id {source_id}, got {question.get('question_id')}")
    return question


def _assert_artifact_question_id(name: str, content: dict[str, Any], source_id: str) -> None:
    if content.get("question_id") != source_id:
        raise ValueError(f"{name} question_id mismatch: {content.get('question_id')}")


def assemble_comprehension_info(
    job: dict[str, Any],
    artifact_dir: Path,
    context: dict[str, Any] | None = None,
) -> None:
    context = context or {}
    source_id = str(job["source_id"])
    logger.info("assemble_comprehension_info: source_id=%s", source_id)
    question = _single_parsed_question(artifact_dir, source_id)
    key_info = _load_json_object(artifact_dir / "key_info_reviewed.json")
    possible_errors = _load_json_object(artifact_dir / "possible_errors_reviewed.json")
    difficulty = _load_json_object(artifact_dir / "comprehension_difficulty.json")

    logger.info("  validating input artifacts")
    for name, content in (
        ("key_info_reviewed.json", key_info),
        ("possible_errors_reviewed.json", possible_errors),
        ("comprehension_difficulty.json", difficulty),
    ):
        check_cancellation(context)
        _assert_artifact_question_id(name, content, source_id)

    fingerprint = question.get("fingerprint")
    if fingerprint is not None and not isinstance(fingerprint, str):
        raise ValueError("fingerprint must be a string or null")

    comprehension_data = {
        "fingerprint": fingerprint,
        "comprehension_difficulty": difficulty.get("comprehension_difficulty"),
        "key_info_list": key_info.get("key_info_list", []),
        "possible_error_list": possible_errors.get("possible_error_list", []),
    }
    payload = {
        "question_id": source_id,
        "fingerprint": fingerprint,
        "fingerprint_source": question.get("fingerprint_source", "missing"),
        "fingerprint_missing": fingerprint is None,
        "comprehension_data": comprehension_data,
    }
    manifest = {
        "question_id": source_id,
        "workflow_key": job.get("workflow_key", "question_comprehension_info"),
        "source_type": job.get("source_type", "question"),
        "title": job.get("title", ""),
        "fingerprint": fingerprint,
        "fingerprint_missing": fingerprint is None,
        "artifacts": {
            "comprehension_info.json": {"present": True},
        },
        "skill_versions": collect_skill_versions(str(job.get("id", "")), context),
    }

    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "comprehension_info.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (artifact_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("  wrote comprehension_info.json and manifest.json")
