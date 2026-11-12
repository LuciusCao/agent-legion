#!/usr/bin/env python3
"""review-questions 输出校验：声明产物质量与 Agent 过程合规。

Agent 自查显式设置 AGENT_LEGION_VALIDATE_CONTEXT=agent；Host 复验由平台
注入 host，只检查声明 inputs/outputs。变量缺失保留完整检查的旧行为。
只依赖标准库。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

OVERALL_VERDICTS = ("pass", "revise")
ITEM_VERDICTS = ("pass", "fail")


def fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def _load_json(path: Path, label: str):
    if not path.is_file():
        raise FileNotFoundError(f"missing required file: {label}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON: {exc}") from exc


def _check_agent_process(job_dir: Path, expected_ids: list[str]) -> int | None:
    """Check undeclared work evidence only where the Agent can produce it."""
    if os.environ.get("AGENT_LEGION_VALIDATE_CONTEXT") == "host":
        return None
    label = ".work/review-questions.json"
    try:
        process = _load_json(job_dir / label, label)
    except (FileNotFoundError, ValueError) as exc:
        return fail(str(exc))
    verified_ids = process.get("answer_verified_ids") if isinstance(process, dict) else None
    if (
        not isinstance(verified_ids, list)
        or not all(isinstance(item, str) for item in verified_ids)
        or sorted(verified_ids) != expected_ids
    ):
        return fail(f"{label}.answer_verified_ids must cover every exercise exactly once")
    return None


def main() -> int:
    if len(sys.argv) != 2:
        return fail("usage: validate_output.py <job_dir>")
    job_dir = Path(sys.argv[1])
    try:
        review = _load_json(job_dir / "exercises_review.json", "exercises_review.json")
        exercises_payload = _load_json(job_dir / "exercises.json", "exercises.json")
    except (FileNotFoundError, ValueError) as exc:
        return fail(str(exc))
    if not isinstance(review, dict):
        return fail("exercises_review.json must contain a JSON object")

    if review.get("verdict") not in OVERALL_VERDICTS:
        return fail(f"verdict must be one of {OVERALL_VERDICTS}")
    if not str(review.get("summary") or "").strip():
        return fail("summary must be non-empty")

    item_reviews = review.get("exercise_reviews")
    if not isinstance(item_reviews, list):
        return fail("exercise_reviews must be an array")
    for index, item in enumerate(item_reviews):
        if not isinstance(item, dict):
            return fail(f"exercise_reviews[{index}] must be an object")
        if not str(item.get("id") or "").strip():
            return fail(f"exercise_reviews[{index}].id must be non-empty")
        if item.get("verdict") not in ITEM_VERDICTS:
            return fail(f"exercise_reviews[{index}].verdict must be one of {ITEM_VERDICTS}")
        issues = item.get("issues")
        if not isinstance(issues, list) or not all(isinstance(i, str) for i in issues):
            return fail(f"exercise_reviews[{index}].issues must be an array of strings")

    exercises = exercises_payload.get("exercises") if isinstance(exercises_payload, dict) else None
    expected_ids = sorted(str(e.get("id")) for e in exercises or [] if isinstance(e, dict))
    reviewed_ids = sorted(str(item["id"]) for item in item_reviews)
    if reviewed_ids != expected_ids:
        return fail(
            f"exercise_reviews ids {reviewed_ids} do not match exercises ids {expected_ids}"
        )
    return _check_agent_process(job_dir, expected_ids) or 0


if __name__ == "__main__":
    sys.exit(main())
