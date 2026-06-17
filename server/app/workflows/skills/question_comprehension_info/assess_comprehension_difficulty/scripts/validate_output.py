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
    validate_question_id,
)

REQUIRED_SIGNAL_KEYS = (
    "key_info_count",
    "hidden_info_count",
    "possible_error_count",
    "ability_count",
)


def _validate_non_negative_int(value: object, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ContractError(f"{field_name} must be a non-negative int, got {value!r}")


def _validate_evidence(value: object) -> None:
    if not isinstance(value, list) or len(value) == 0:
        raise ContractError("evidence must be a non-empty list of strings")
    for i, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ContractError(f"evidence[{i}] must be a non-empty string")


def _load_key_info_counts(job_dir: Path) -> dict[str, int]:
    path = job_dir / "key_info_reviewed.json"
    if not path.is_file():
        raise ContractError("Missing input file: key_info_reviewed.json")

    data = load_json_object(path)
    key_info_list = data.get("key_info_list")
    if not isinstance(key_info_list, list):
        raise ContractError("key_info_reviewed.json 'key_info_list' must be an array")

    total = len(key_info_list)
    hidden = sum(
        1 for item in key_info_list if isinstance(item, dict) and item.get("type") == "hidden"
    )

    ability_ids: set[str] = set()
    for item in key_info_list:
        if not isinstance(item, dict):
            continue
        abilities = item.get("question_comprehension_abilities")
        if isinstance(abilities, list):
            for ability_id in abilities:
                if isinstance(ability_id, str):
                    ability_ids.add(ability_id)

    return {
        "key_info_count": total,
        "hidden_info_count": hidden,
        "ability_count": len(ability_ids),
    }


def _load_possible_error_count(job_dir: Path) -> int:
    path = job_dir / "possible_errors_reviewed.json"
    if not path.is_file():
        raise ContractError("Missing input file: possible_errors_reviewed.json")

    data = load_json_object(path)
    possible_error_list = data.get("possible_error_list")
    if not isinstance(possible_error_list, list):
        raise ContractError("possible_errors_reviewed.json 'possible_error_list' must be an array")
    return len(possible_error_list)


def _validate_signals(signals: object, job_dir: Path, cross_check: bool) -> None:
    if not isinstance(signals, dict):
        raise ContractError(f"signals must be an object, got {type(signals).__name__}")

    for key in REQUIRED_SIGNAL_KEYS:
        value = signals.get(key)
        _validate_non_negative_int(value, f"signals.{key}")

    if cross_check:
        try:
            key_info_counts = _load_key_info_counts(job_dir)
            possible_error_count = _load_possible_error_count(job_dir)
        except ContractError as exc:
            raise ContractError(f"Cannot cross-check signals: {exc}") from exc

        expected = {
            "key_info_count": key_info_counts["key_info_count"],
            "hidden_info_count": key_info_counts["hidden_info_count"],
            "possible_error_count": possible_error_count,
            "ability_count": key_info_counts["ability_count"],
        }

        for key in REQUIRED_SIGNAL_KEYS:
            actual = signals[key]
            exp = expected[key]
            if actual != exp:
                raise ContractError(
                    f"signals.{key} ({actual}) does not match reviewed inputs ({exp})"
                )


def _validate_difficulty_payload(
    job_dir: Path, payload: dict[str, Any], question: dict[str, Any], cross_check: bool
) -> None:
    validate_question_id(payload, question)

    score = payload.get("comprehension_difficulty")
    if not isinstance(score, int) or isinstance(score, bool) or not 1 <= score <= 99:
        raise ContractError(f"comprehension_difficulty must be in range 1..99, got {score!r}")

    _validate_signals(payload.get("signals"), job_dir, cross_check)
    _validate_evidence(payload.get("evidence"))


def _validate_report(job_dir: Path, report: dict[str, Any], question: dict[str, Any]) -> None:
    validate_question_id(report, question)

    warnings = report.get("warnings")
    if not isinstance(warnings, list):
        raise ContractError("report warnings must be an array")
    for i, item in enumerate(warnings):
        if not isinstance(item, str) or not item.strip():
            raise ContractError(f"report warnings[{i}] must be a non-empty string")

    method = report.get("method")
    if not isinstance(method, str) or not method.strip():
        raise ContractError("report method must be a non-empty string")


def validate(job_dir: Path, *, cross_check: bool = True) -> list[str]:
    errors: list[str] = []

    try:
        question = load_single_question(job_dir / "questions_parsed.json")
    except ContractError as exc:
        return [str(exc)]

    difficulty_path = job_dir / "comprehension_difficulty.json"
    if not difficulty_path.is_file():
        return ["Missing output file: comprehension_difficulty.json"]

    report_path = job_dir / "comprehension_difficulty_report.json"
    if not report_path.is_file():
        return ["Missing output file: comprehension_difficulty_report.json"]

    try:
        payload = load_json_object(difficulty_path)
        _validate_difficulty_payload(job_dir, payload, question, cross_check)
    except ContractError as exc:
        errors.append(str(exc))

    try:
        report = load_json_object(report_path)
        _validate_report(job_dir, report, question)
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
