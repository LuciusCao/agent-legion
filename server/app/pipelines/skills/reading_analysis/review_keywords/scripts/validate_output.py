#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from review import validate_review_result  # noqa: E402
from validation import (  # noqa: E402
    ContractError,
    load_json_object,
    load_single_question,
    validate_confidence,
    validate_question_id,
    validate_source_location,
    validate_unique_ids,
)


def _validate_non_empty_string(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field_name} must be a non-empty string, got {value!r}")


def _validate_keywords_raw(job_dir: Path, question: dict[str, Any]) -> None:
    path = job_dir / "keywords_raw.json"
    if not path.is_file():
        raise ContractError("Missing output file: keywords_raw.json")

    raw = load_json_object(path)
    validate_question_id(raw, question)

    keywords = raw.get("keywords")
    if not isinstance(keywords, list):
        raise ContractError("keywords_raw.json 'keywords' must be a list")

    for kw in keywords:
        for field in (
            "id",
            "source_text",
            "normalized_text",
            "location",
            "necessity",
            "counterfactual",
            "confidence",
        ):
            if field not in kw:
                raise ContractError(f"keyword missing required field: {field}")

    for kw in keywords:
        _validate_non_empty_string(kw["normalized_text"], "normalized_text")
        _validate_non_empty_string(kw["necessity"], "necessity")
        _validate_non_empty_string(kw["counterfactual"], "counterfactual")

    for kw in keywords:
        validate_source_location(question, kw["source_text"], kw["location"])

    for kw in keywords:
        validate_confidence(kw["confidence"])

    validate_unique_ids(keywords, "keyword")


def validate(job_dir: Path) -> list[str]:
    errors: list[str] = []

    try:
        question = load_single_question(job_dir / "questions_parsed.json")
    except ContractError as exc:
        return [str(exc)]

    try:
        _validate_keywords_raw(job_dir, question)
    except ContractError as exc:
        errors.append(str(exc))

    try:
        validate_review_result(
            source_path=job_dir / "keywords_raw.json",
            reviewed_path=job_dir / "keywords_reviewed.json",
            report_path=job_dir / "keywords_review_report.json",
            exact_copy=True,
        )
    except ContractError as exc:
        errors.append(str(exc))

    return errors


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: validate_output.py <job-directory>", file=sys.stderr)
        sys.exit(1)
    job_dir = Path(sys.argv[1])
    errs = validate(job_dir)
    if errs:
        for e in errs:
            print(e, file=sys.stderr)
        sys.exit(1)
    sys.exit(0)
