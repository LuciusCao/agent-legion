#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from validation import (  # noqa: E402
    ContractError,
    _validate_non_empty_string,
    load_json_object,
    load_single_question,
    load_valid_key_info_ids,
    validate_possible_errors_payload,
    validate_question_id,
)


def _validate_review_report(
    job_dir: Path, question: dict[str, Any], reviewed: dict[str, Any]
) -> None:
    path = job_dir / "possible_errors_review_report.json"
    if not path.is_file():
        raise ContractError("Missing output file: possible_errors_review_report.json")

    report = load_json_object(path)
    validate_question_id(report, question)

    approved_count = report.get("approved_count")
    if (
        not isinstance(approved_count, int)
        or isinstance(approved_count, bool)
        or approved_count < 0
    ):
        raise ContractError(f"approved_count must be a non-negative int, got {approved_count!r}")

    rejected_count = report.get("rejected_count")
    if (
        not isinstance(rejected_count, int)
        or isinstance(rejected_count, bool)
        or rejected_count < 0
    ):
        raise ContractError(f"rejected_count must be a non-negative int, got {rejected_count!r}")

    possible_error_list = reviewed.get("possible_error_list", [])
    if not isinstance(possible_error_list, list):
        raise ContractError("possible_errors_reviewed.json 'possible_error_list' must be an array")

    total = approved_count + rejected_count
    if total != len(possible_error_list):
        raise ContractError(
            f"approved_count + rejected_count ({total}) must equal len(possible_error_list) ({len(possible_error_list)})"
        )

    warnings = report.get("warnings")
    if not isinstance(warnings, list):
        raise ContractError("warnings must be an array")

    decisions = report.get("decisions")
    if not isinstance(decisions, list):
        raise ContractError("decisions must be an array")
    if len(decisions) != total:
        raise ContractError(
            f"len(decisions) ({len(decisions)}) must equal approved_count + rejected_count ({total})"
        )

    seen_ids: set[str] = set()
    for i, decision in enumerate(decisions):
        if not isinstance(decision, dict):
            raise ContractError(f"decision at index {i} must be an object")
        error_id = decision.get("error_id")
        if not isinstance(error_id, str):
            raise ContractError(f"decision[{i}].error_id must be a string")
        if error_id in seen_ids:
            raise ContractError(f"duplicate decision error_id: {error_id!r}")
        seen_ids.add(error_id)

        decision_value = decision.get("decision")
        if decision_value not in ("approved", "rejected"):
            raise ContractError(
                f"decision[{i}].decision must be 'approved' or 'rejected', got {decision_value!r}"
            )

        _validate_non_empty_string(decision.get("reason"), f"decision[{i}].reason")


def validate(job_dir: Path) -> list[str]:
    errors: list[str] = []

    try:
        question = load_single_question(job_dir / "questions_parsed.json")
    except ContractError as exc:
        return [str(exc)]

    try:
        valid_key_info_ids = load_valid_key_info_ids(job_dir)
    except ContractError as exc:
        return [str(exc)]

    raw_path = job_dir / "possible_errors_raw.json"
    if not raw_path.is_file():
        return ["Missing input file: possible_errors_raw.json"]

    reviewed_path = job_dir / "possible_errors_reviewed.json"
    if not reviewed_path.is_file():
        return ["Missing output file: possible_errors_reviewed.json"]

    reviewed: dict[str, Any] = {}
    try:
        reviewed = load_json_object(reviewed_path)
        validate_question_id(reviewed, question)
        validate_possible_errors_payload(reviewed, valid_key_info_ids)
    except ContractError as exc:
        errors.append(str(exc))

    try:
        _validate_review_report(job_dir, question, reviewed)
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
