from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from server.app.cms.client import get_token
from server.app.cms.question import fetch_question_detail
from server.app.pipelines.question_content import _effective_cms_config


def fetch_questions(
    job: dict[str, Any],
    artifact_dir: Path,
    context: dict[str, Any] | None = None,
) -> None:
    context = context or {}
    settings_config = context.get("settings_config")
    if not isinstance(settings_config, dict):
        settings_config = {}

    cms_config = _effective_cms_config(job, context)
    api_url = cms_config.get("api_url") or cms_config.get("question_detail_url")
    if api_url:
        token = get_token(str(cms_config.get("env", "")), cms_config)
        detail = fetch_question_detail(str(job["source_id"]), str(api_url), token)
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

    data = json.loads(questions_path.read_text(encoding="utf-8"))
    questions = data.get("questions", [])
    if not isinstance(questions, list) or not questions:
        raise ValueError("questions.json contains no questions")

    parsed_questions: list[dict[str, Any]] = []
    for q in questions:
        if not isinstance(q, dict):
            raise ValueError("Invalid question record in questions.json")
        qid = q.get("question_id")
        if not qid:
            raise ValueError("Missing question_id in questions.json")
        normalized = q.get("normalized") or {}
        if not isinstance(normalized, dict):
            normalized = {}
        parsed_questions.append(
            {
                "question_id": str(qid),
                "stem": normalized.get("stem", ""),
                "options": normalized.get("options", []),
                "answer": normalized.get("answer", ""),
                "analysis": normalized.get("analysis", ""),
            }
        )

    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "questions_parsed.json").write_text(
        json.dumps({"questions": parsed_questions}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def mark_question(
    job: dict[str, Any],
    artifact_dir: Path,
    context: dict[str, Any] | None = None,
) -> None:
    inputs = {
        "keywords": artifact_dir / "keywords_reviewed.json",
        "difficulty": artifact_dir / "difficulty_reviewed.json",
        "distractors": artifact_dir / "distractors_reviewed.json",
    }

    data: dict[str, Any] = {}
    for key, path in inputs.items():
        if not path.is_file():
            raise ValueError(f"Missing input: {path.name}")
        content = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(content, dict):
            raise ValueError(f"Invalid content in {path.name}")
        qid = content.get("question_id")
        if not isinstance(qid, str):
            raise ValueError(f"Missing question_id in {path.name}")
        if qid not in data:
            data[qid] = {"question_id": qid}
        if key == "keywords":
            data[qid]["keywords"] = content.get("keywords", [])
        elif key == "difficulty":
            data[qid]["reading_difficulty"] = content.get("reading_difficulty")
        elif key == "distractors":
            data[qid]["distractors"] = content.get("distractors", [])

    source_id = str(job["source_id"])
    if source_id not in data:
        raise ValueError(f"Missing question_id {source_id} in reviewed artifacts")

    for _qid, record in data.items():
        if record.get("question_id") != source_id:
            raise ValueError(
                f"Mismatched question_id in reviewed artifacts: {record.get('question_id')}"
            )

    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "question_marks.json").write_text(
        json.dumps({"questions": [data[source_id]]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
