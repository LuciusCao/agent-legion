#!/usr/bin/env python3
"""review-script 输出校验：script_review.json 结构合规。

用法：python validate_output.py <job_dir>；退出码 0 = 通过。
只依赖标准库。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

VERDICTS = ("pass", "revise")
DIMENSIONS = ("teaching_goal", "accuracy", "pacing")
ISSUE_KEYS = ("section", "problem", "suggestion")


def fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def main() -> int:
    if len(sys.argv) != 2:
        return fail("usage: validate_output.py <job_dir>")
    review_path = Path(sys.argv[1]) / "script_review.json"
    if not review_path.is_file():
        return fail("missing required output: script_review.json")
    try:
        review = json.loads(review_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return fail(f"script_review.json is not valid JSON: {exc}")
    if not isinstance(review, dict):
        return fail("script_review.json must contain a JSON object")

    if review.get("verdict") not in VERDICTS:
        return fail(f"verdict must be one of {VERDICTS}")

    dimensions = review.get("dimensions")
    if not isinstance(dimensions, dict) or sorted(dimensions) != sorted(DIMENSIONS):
        return fail(f"dimensions must contain exactly: {', '.join(DIMENSIONS)}")
    for name in DIMENSIONS:
        entry = dimensions[name]
        if not isinstance(entry, dict):
            return fail(f"dimensions.{name} must be an object")
        score = entry.get("score")
        if not isinstance(score, int) or isinstance(score, bool) or not 1 <= score <= 10:
            return fail(f"dimensions.{name}.score must be an integer in [1, 10]")
        if not str(entry.get("comment") or "").strip():
            return fail(f"dimensions.{name}.comment must be non-empty")

    issues = review.get("issues")
    if not isinstance(issues, list):
        return fail("issues must be an array")
    for index, issue in enumerate(issues):
        if not isinstance(issue, dict):
            return fail(f"issues[{index}] must be an object")
        for key in ISSUE_KEYS:
            if not str(issue.get(key) or "").strip():
                return fail(f"issues[{index}].{key} must be non-empty")

    if not str(review.get("summary") or "").strip():
        return fail("summary must be non-empty")
    return 0


if __name__ == "__main__":
    sys.exit(main())
