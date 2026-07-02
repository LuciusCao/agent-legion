from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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
