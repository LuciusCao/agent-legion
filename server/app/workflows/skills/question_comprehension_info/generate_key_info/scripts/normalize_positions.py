#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from validation import (  # noqa: E402
    ContractError,
    load_json_object,
    load_single_question,
    normalize_key_info_positions,
)


def normalize(job_dir: Path) -> list[str]:
    """Convert HTML-based positions in key_info_raw.json to plain-text positions."""
    try:
        question = load_single_question(job_dir / "questions_parsed.json")
    except ContractError as exc:
        return [str(exc)]

    raw_path = job_dir / "key_info_raw.json"
    if not raw_path.is_file():
        return ["Missing input file: key_info_raw.json"]

    try:
        raw = load_json_object(raw_path)
    except ContractError as exc:
        return [str(exc)]

    warnings: list[str] = normalize_key_info_positions(raw, question)
    raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return warnings


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: normalize_positions.py <job-directory>", file=sys.stderr)
        sys.exit(1)

    job_dir = Path(sys.argv[1])
    warns = normalize(job_dir)
    for warning in warns:
        print(warning, file=sys.stderr)
    sys.exit(0)
