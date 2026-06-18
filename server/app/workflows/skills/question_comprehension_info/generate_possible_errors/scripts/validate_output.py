#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from validation import (  # noqa: E402
    ContractError,
    load_json_object,
    load_single_question,
    load_valid_key_info_ids,
    validate_possible_errors_payload,
    validate_question_id,
)


def _validate_report(job_dir: Path, question: dict[str, Any]) -> None:
    path = job_dir / "possible_errors_report.json"
    if not path.is_file():
        raise ContractError("Missing output file: possible_errors_report.json")

    report = load_json_object(path)
    validate_question_id(report, question)

    warnings = report.get("warnings")
    if not isinstance(warnings, list):
        raise ContractError("possible_errors_report.json 'warnings' must be an array")
    for i, warning in enumerate(warnings):
        if not isinstance(warning, str):
            raise ContractError(f"warning at index {i} must be a string")


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
        return ["Missing output file: possible_errors_raw.json"]

    try:
        raw = load_json_object(raw_path)
        validate_question_id(raw, question)
        validate_possible_errors_payload(raw, valid_key_info_ids)
    except ContractError as exc:
        errors.append(str(exc))

    try:
        _validate_report(job_dir, question)
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
