from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from server.app.executors.cancellation import check_cancellation
from server.app.workflows.comprehension_common import (
    _assert_artifact_question_id,
    _load_json_object,
    _single_parsed_question,
)
from server.app.workflows.comprehension_contract import assert_comprehension_lists_contract
from server.app.workflows.comprehension_eligibility import (
    classify_comprehension_eligibility,
    finalize_non_uploadable,
)
from server.app.workflows.question_fingerprint import (
    compute_question_fingerprint,
    extract_cms_fingerprint,
)
from server.app.workflows.skill_version_collection import collect_skill_versions
from server.app.workflows.workflow_manifest import workflow_manifest

__all__ = ["classify_comprehension_eligibility", "finalize_non_uploadable"]

logger = logging.getLogger(__name__)


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
    parsed = {"questions": parsed_questions}
    out_path = artifact_dir / "questions_parsed.json"
    out_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("  wrote %s", out_path.name)
    lean = {"questions": _strip_analysis(parsed_questions)}
    lean_path = artifact_dir / "questions_parsed_lean.json"
    lean_path.write_text(json.dumps(lean, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("  wrote %s", lean_path.name)


def _strip_analysis(parsed_questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a copy of parsed questions with the verbose analysis field removed."""
    return [{k: v for k, v in q.items() if k != "analysis"} for q in parsed_questions]


def assemble_comprehension_info(
    job: dict[str, Any],
    artifact_dir: Path,
    context: dict[str, Any] | None = None,
) -> None:
    context = context or {}
    source_id = str(job["source_id"])
    logger.info("assemble_comprehension_info: source_id=%s", source_id)
    question = _single_parsed_question(artifact_dir, source_id, "questions_parsed_lean.json")
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

    key_info_list = key_info.get("key_info_list", [])
    possible_error_list = possible_errors.get("possible_error_list", [])
    check_cancellation(context)
    assert_comprehension_lists_contract(key_info_list, possible_error_list)

    comprehension_data = {
        "fingerprint": fingerprint,
        "comprehension_difficulty": difficulty.get("comprehension_difficulty"),
        "key_info_list": key_info_list,
        "possible_error_list": possible_error_list,
    }
    payload = {
        "question_id": source_id,
        "fingerprint": fingerprint,
        "fingerprint_source": question.get("fingerprint_source", "missing"),
        "fingerprint_missing": fingerprint is None,
        "schema_version": "v1",
        "comprehension_data": comprehension_data,
    }
    manifest = {
        "question_id": source_id,
        "workflow_key": job.get("workflow_key", "question_comprehension_info"),
        "workflow": workflow_manifest(job, "question_comprehension_info"),
        "source_type": job.get("source_type", "question"),
        "title": job.get("title", ""),
        "fingerprint": fingerprint,
        "fingerprint_missing": fingerprint is None,
        "artifacts": {
            "comprehension_info.json": {"present": True},
        },
        "skill_versions": collect_skill_versions(str(job.get("id", "")), context, job),
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
