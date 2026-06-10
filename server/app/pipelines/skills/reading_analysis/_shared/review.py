from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

try:
    from server.app.pipelines.skills.reading_analysis._shared.validation import (
        ContractError,
        _normalize_json,
        load_json_object,
        validate_review_hash,
    )
except ModuleNotFoundError:
    from validation import (  # type: ignore[no-redef]
        ContractError,
        _normalize_json,
        load_json_object,
        validate_review_hash,
    )


def validate_review_result(
    *,
    source_path: Path,
    reviewed_path: Path,
    report_path: Path,
    exact_copy: bool,
    projection: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> None:
    """Validate a review report and its relationship to source and reviewed artifacts.

    Rules:
    - status is exactly "passed" or "failed"
    - report question_id equals source question_id
    - report hash matches source file SHA-256
    - checks and issues are arrays
    - failed requires at least one issue and reviewed artifact absence
    - passed requires no issues and reviewed artifact presence
    - exact_copy=True requires semantic JSON equality
    - projection requires reviewed JSON to equal projection(raw) exactly
    """
    report = load_json_object(report_path)
    source = load_json_object(source_path)

    status = report.get("status")
    if status not in ("passed", "failed"):
        raise ContractError(f"report status must be 'passed' or 'failed', got {status!r}")

    source_question_id = source.get("question_id")
    report_question_id = report.get("question_id")
    if report_question_id != source_question_id:
        raise ContractError(
            f"report question_id mismatch: report={report_question_id!r}, source={source_question_id!r}"
        )

    validate_review_hash(source_path, report)

    checks = report.get("checks", [])
    issues = report.get("issues", [])
    if not isinstance(checks, list):
        raise ContractError(f"report checks must be an array, got {type(checks).__name__}")
    if not isinstance(issues, list):
        raise ContractError(f"report issues must be an array, got {type(issues).__name__}")

    reviewed_exists = reviewed_path.is_file()

    if status == "failed":
        if not issues:
            raise ContractError("failed report must contain at least one issue")
        if reviewed_exists:
            raise ContractError("failed review must not produce a reviewed artifact")
    else:  # passed
        if issues:
            raise ContractError("passed report must contain no issues")
        if not reviewed_exists:
            raise ContractError("passed review requires a reviewed artifact")

    if reviewed_exists:
        reviewed = load_json_object(reviewed_path)
        if exact_copy:
            if _normalize_json(source) != _normalize_json(reviewed):
                raise ContractError(
                    "reviewed artifact must be an exact copy of the source artifact"
                )
        elif projection is not None:
            expected = projection(source)
            if _normalize_json(expected) != _normalize_json(reviewed):
                raise ContractError("reviewed artifact does not match the expected projection")
