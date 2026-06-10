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
    validate_question_id,
    validate_score_1_99,
)

DIMENSION_KEYS = [
    "knowledge_complexity",
    "reasoning_steps",
    "calculation_load",
    "reading_filter_load",
]


def _validate_weights(weights: dict[str, Any]) -> None:
    if set(weights.keys()) != set(DIMENSION_KEYS):
        raise ContractError(
            f"weights must contain exactly keys {DIMENSION_KEYS}, got {list(weights.keys())}"
        )
    for key in DIMENSION_KEYS:
        value = weights[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ContractError(f"weight {key} must be a number, got {type(value).__name__}")
        if not (0 <= value <= 1):
            raise ContractError(f"weight {key} must be in [0, 1], got {value!r}")
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-9:
        raise ContractError(f"weights must sum to 1.0, got {total}")


def _validate_difficulty_raw(job_dir: Path, question: dict[str, Any]) -> None:
    path = job_dir / "difficulty_raw.json"
    if not path.is_file():
        raise ContractError("Missing output file: difficulty_raw.json")

    data = load_json_object(path)
    validate_question_id(data, question)

    dimensions = data.get("dimensions")
    if not isinstance(dimensions, dict):
        raise ContractError("difficulty_raw.json 'dimensions' must be a dict")
    if set(dimensions.keys()) != set(DIMENSION_KEYS):
        raise ContractError(
            f"dimensions must contain exactly keys {DIMENSION_KEYS}, got {list(dimensions.keys())}"
        )
    for key in DIMENSION_KEYS:
        validate_score_1_99(dimensions[key], key)

    weights = data.get("weights")
    if not isinstance(weights, dict):
        raise ContractError("difficulty_raw.json 'weights' must be a dict")
    _validate_weights(weights)

    reading_difficulty = data.get("reading_difficulty")
    validate_score_1_99(reading_difficulty, "reading_difficulty")

    weighted_sum = sum(dimensions[k] * weights[k] for k in DIMENSION_KEYS)
    expected = round(weighted_sum)
    if reading_difficulty != expected:
        raise ContractError(
            f"reading_difficulty mismatch: expected {expected} "
            f"(from dimensions and weights), got {reading_difficulty}"
        )

    evidence = data.get("evidence")
    if not isinstance(evidence, dict):
        raise ContractError("difficulty_raw.json 'evidence' must be a dict")
    if set(evidence.keys()) != set(DIMENSION_KEYS):
        raise ContractError(
            f"evidence must contain exactly keys {DIMENSION_KEYS}, got {list(evidence.keys())}"
        )
    for key in DIMENSION_KEYS:
        items = evidence[key]
        if not isinstance(items, list) or len(items) == 0:
            raise ContractError(f"evidence[{key!r}] must be a non-empty list")
        for item in items:
            if not isinstance(item, str) or not item.strip():
                raise ContractError(f"evidence[{key!r}] must contain non-empty strings")


def cms_projection(raw: dict[str, Any]) -> dict[str, Any]:
    return {"question_id": raw["question_id"], "reading_difficulty": raw["reading_difficulty"]}


def validate(job_dir: Path) -> None:
    question = load_single_question(job_dir / "questions_parsed.json")
    _validate_difficulty_raw(job_dir, question)

    raw_path = job_dir / "difficulty_raw.json"
    reviewed_path = job_dir / "difficulty_reviewed.json"
    report_path = job_dir / "difficulty_review_report.json"

    validate_review_result(
        source_path=raw_path,
        reviewed_path=reviewed_path,
        report_path=report_path,
        exact_copy=False,
        projection=cms_projection,
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: validate_output.py <job-directory>", file=sys.stderr)
        sys.exit(1)

    try:
        validate(Path(sys.argv[1]))
    except ContractError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
