"""question_comprehension_info node: clean and parse the fetched questions.

Reads ``questions.json`` (written by the intake node), normalizes each
question, computes a fingerprint (CMS-provided or md5 fallback), and writes
``questions_parsed.json`` plus the lean variant without ``analysis``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from server.app.executors.cancellation import check_cancellation
from server.app.workflows.question_fingerprint import (
    compute_question_fingerprint,
    extract_cms_fingerprint,
)

logger = logging.getLogger(__name__)


def _strip_analysis(parsed_questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a copy of parsed questions with the verbose analysis field removed."""
    return [{k: v for k, v in q.items() if k != "analysis"} for q in parsed_questions]


def run(
    job: dict[str, Any],
    job_dir: Path,
    runtime: dict[str, Any] | None = None,
) -> None:
    context = runtime or {}
    questions_path = job_dir / "questions.json"
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
    job_dir.mkdir(parents=True, exist_ok=True)
    parsed = {"questions": parsed_questions}
    out_path = job_dir / "questions_parsed.json"
    out_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("  wrote %s", out_path.name)
    lean = {"questions": _strip_analysis(parsed_questions)}
    lean_path = job_dir / "questions_parsed_lean.json"
    lean_path.write_text(json.dumps(lean, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("  wrote %s", lean_path.name)
