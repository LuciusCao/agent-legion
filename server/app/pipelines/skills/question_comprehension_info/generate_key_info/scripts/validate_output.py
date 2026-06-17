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
    load_valid_ability_ids,
    validate_key_info_payload,
    validate_question_id,
)


def _validate_report(job_dir: Path, question: dict[str, Any]) -> None:
    path = job_dir / "key_info_report.json"
    if not path.is_file():
        raise ContractError("Missing output file: key_info_report.json")

    report = load_json_object(path)
    validate_question_id(report, question)

    warnings = report.get("warnings")
    if not isinstance(warnings, list):
        raise ContractError("key_info_report.json 'warnings' must be an array")
    for i, warning in enumerate(warnings):
        if not isinstance(warning, str):
            raise ContractError(f"warning at index {i} must be a string")


def validate(job_dir: Path) -> list[str]:
    errors: list[str] = []

    abilities_path = (
        Path(__file__).resolve().parents[2]
        / "_shared"
        / "references"
        / "question_comprehension_abilities.json"
    )
    try:
        valid_ability_ids = load_valid_ability_ids(abilities_path)
    except ContractError as exc:
        return [str(exc)]

    try:
        question = load_single_question(job_dir / "questions_parsed.json")
    except ContractError as exc:
        return [str(exc)]

    raw_path = job_dir / "key_info_raw.json"
    if not raw_path.is_file():
        return ["Missing output file: key_info_raw.json"]

    try:
        raw = load_json_object(raw_path)
        validate_key_info_payload(raw, question, valid_ability_ids)
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
