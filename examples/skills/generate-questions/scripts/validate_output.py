#!/usr/bin/env python3
"""generate-questions 输出校验：exercises.json 题量/难度/字段合规。

用法：python validate_output.py <job_dir>；退出码 0 = 通过。
只依赖标准库。
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

EXPECTED_COUNT = 5
EXPECTED_DIFFICULTIES = {"easy": 2, "medium": 2, "hard": 1}
REQUIRED_FIELDS = ("id", "difficulty", "stem", "answer", "analysis")


def fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def _load_json(path: Path, label: str):
    if not path.is_file():
        raise FileNotFoundError(f"missing required output: {label}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON: {exc}") from exc


def main() -> int:
    if len(sys.argv) != 2:
        return fail("usage: validate_output.py <job_dir>")
    job_dir = Path(sys.argv[1])
    try:
        payload = _load_json(job_dir / "exercises.json", "exercises.json")
    except (FileNotFoundError, ValueError) as exc:
        return fail(str(exc))
    if not isinstance(payload, dict):
        return fail("exercises.json must contain a JSON object")

    exercises = payload.get("exercises")
    if not isinstance(exercises, list) or len(exercises) != EXPECTED_COUNT:
        return fail(f"exercises must contain exactly {EXPECTED_COUNT} items")

    expected_ids = [f"q{index}" for index in range(1, EXPECTED_COUNT + 1)]
    difficulties: Counter[str] = Counter()
    for index, (exercise, expected_id) in enumerate(zip(exercises, expected_ids, strict=True)):
        if not isinstance(exercise, dict):
            return fail(f"exercises[{index}] must be an object")
        for field in REQUIRED_FIELDS:
            value = exercise.get(field)
            if not isinstance(value, str) or not value.strip():
                return fail(f"exercises[{index}].{field} must be a non-empty string")
        if exercise["id"] != expected_id:
            return fail(f"exercises[{index}].id must be {expected_id!r}")
        if exercise["difficulty"] not in EXPECTED_DIFFICULTIES:
            return fail(
                f"exercises[{index}].difficulty must be one of {sorted(EXPECTED_DIFFICULTIES)}"
            )
        difficulties[exercise["difficulty"]] += 1
    if dict(difficulties) != EXPECTED_DIFFICULTIES:
        return fail(f"difficulty distribution {dict(difficulties)} != {EXPECTED_DIFFICULTIES}")

    knowledge_path = job_dir / "knowledge_point.json"
    if knowledge_path.is_file():
        try:
            knowledge = _load_json(knowledge_path, "knowledge_point.json")
        except ValueError as exc:
            return fail(str(exc))
        expected_kp = str(knowledge.get("knowledge_point", {}).get("id") or "")
        if expected_kp and payload.get("knowledge_point_id") != expected_kp:
            return fail(
                f"knowledge_point_id {payload.get('knowledge_point_id')!r} != input {expected_kp!r}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
