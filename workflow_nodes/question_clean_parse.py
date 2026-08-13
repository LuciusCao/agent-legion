"""question_comprehension_info node: clean and parse the fetched questions.

Reads ``questions.json`` (written by the intake node), normalizes each
question, computes a fingerprint (CMS-provided or md5 fallback), and writes
``questions_parsed.json`` plus the lean variant without ``analysis``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workspace_libs.node_sdk import NodeContext
from workspace_libs.question_fingerprint import (
    compute_question_fingerprint,
    extract_cms_fingerprint,
)


def _strip_analysis(parsed_questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a copy of parsed questions with the verbose analysis field removed."""
    return [{k: v for k, v in q.items() if k != "analysis"} for q in parsed_questions]


def run(
    job: dict[str, Any],
    job_dir: Path,
    runtime: dict[str, Any] | None = None,
) -> None:
    ctx = NodeContext(job, job_dir, runtime)
    log = ctx.logger
    if not ctx.artifacts.path("questions.json").is_file():
        raise ValueError("questions.json not found")

    ctx.checkpoint()
    log.info("clean_and_parse: source_id=%s", job["source_id"])
    data = ctx.artifacts.read_json("questions.json")
    questions = data.get("questions", [])
    if not isinstance(questions, list) or not questions:
        raise ValueError("questions.json contains no questions")

    log.info("  parsing %d question(s)", len(questions))
    parsed_questions: list[dict[str, Any]] = []
    for q in questions:
        ctx.checkpoint()
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
        log.info(
            "    parsed question_id=%s fingerprint=%s source=%s",
            qid,
            "present" if fingerprint else "missing",
            source,
        )
    parsed = {"questions": parsed_questions}
    out_path = ctx.artifacts.write_json("questions_parsed.json", parsed)
    log.info("  wrote %s", out_path.name)
    lean = {"questions": _strip_analysis(parsed_questions)}
    lean_path = ctx.artifacts.write_json("questions_parsed_lean.json", lean)
    log.info("  wrote %s", lean_path.name)
