#!/usr/bin/env python3
import json
import sys
from pathlib import Path

REQUIRED_OUTPUTS = [
    "difficulty_raw.json",
    "difficulty_report.json",
]


def validate(path: Path) -> list[str]:
    errors: list[str] = []
    for name in REQUIRED_OUTPUTS:
        file_path = path / name
        if not file_path.is_file():
            errors.append(f"Missing output file: {name}")
            continue
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"Invalid JSON in {name}: {exc}")
            continue
        if not isinstance(data, dict):
            errors.append(f"{name} top level must be a JSON object")
            continue
        if "questions" not in data:
            errors.append(f"{name} must contain 'questions' key")
            continue
        if not isinstance(data["questions"], list):
            errors.append(f"{name} 'questions' must be an array")
            continue
        if name.endswith("_report.json"):
            summary = data.get("summary")
            if not isinstance(summary, dict):
                errors.append(f"{name} must contain 'summary' object")
                continue
            if "total" not in summary:
                errors.append(f"{name} summary must contain 'total'")
            if "warnings" not in summary:
                errors.append(f"{name} summary must contain 'warnings'")
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
