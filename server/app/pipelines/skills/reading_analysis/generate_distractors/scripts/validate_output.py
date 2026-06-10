#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))

from validation import (  # type: ignore[import-not-found]
    ContractError,
    load_json_object,
    load_single_question,
    validate_confidence,
    validate_question_id,
    validate_score_1_99,
    validate_source_location,
    validate_unique_ids,
)

REQUIRED_OUTPUTS = [
    "distractors_raw.json",
    "distractors_report.json",
]

REQUIRED_DISTRACTOR_FIELDS = [
    "id",
    "source_text",
    "normalized_text",
    "location",
    "relevance",
    "non_necessity",
    "counterfactual",
    "confusion_strength",
    "confidence",
]


def _validate_non_empty_string(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field_name} must be a non-empty string, got {value!r}")


def _locations_overlap(loc1: dict, loc2: dict) -> bool:
    if loc1.get("source") != loc2.get("source"):
        return False
    s1 = loc1.get("start", 0)
    e1 = loc1.get("end", 0)
    s2 = loc2.get("start", 0)
    e2 = loc2.get("end", 0)
    return bool(s1 < e2 and s2 < e1)


def validate(job_dir: Path) -> list[str]:
    errors: list[str] = []

    for name in REQUIRED_OUTPUTS:
        file_path = job_dir / name
        if not file_path.is_file():
            errors.append(f"Missing output file: {name}")

    if errors:
        return errors

    # Load source question
    try:
        question = load_single_question(job_dir / "questions_parsed.json")
    except ContractError as exc:
        return [str(exc)]

    # Load keywords
    try:
        keywords_data = load_json_object(job_dir / "keywords_reviewed.json")
        keywords = keywords_data.get("keywords", [])
        if not isinstance(keywords, list):
            raise ContractError("keywords_reviewed.json 'keywords' must be a list")
    except ContractError as exc:
        return [str(exc)]

    # Validate distractors_raw.json
    try:
        raw = load_json_object(job_dir / "distractors_raw.json")
        validate_question_id(raw, question)

        distractors = raw.get("distractors")
        if not isinstance(distractors, list):
            raise ContractError("distractors_raw.json 'distractors' must be a list")

        for d in distractors:
            for field in REQUIRED_DISTRACTOR_FIELDS:
                if field not in d:
                    raise ContractError(f"distractor missing required field: {field}")

        for d in distractors:
            _validate_non_empty_string(d["relevance"], "relevance")
            _validate_non_empty_string(d["non_necessity"], "non_necessity")
            _validate_non_empty_string(d["counterfactual"], "counterfactual")

        for d in distractors:
            validate_source_location(question, d["source_text"], d["location"])

        for d in distractors:
            validate_confidence(d["confidence"])
            validate_score_1_99(d["confusion_strength"], "confusion_strength")

        validate_unique_ids(distractors, "distractor")

        seen_texts: set[str] = set()
        for d in distractors:
            text = d["source_text"]
            if text in seen_texts:
                raise ContractError(f"duplicate distractor source_text: {text!r}")
            seen_texts.add(text)

        for d in distractors:
            d_text = d["source_text"]
            d_loc = d["location"]
            for kw in keywords:
                kw_text = kw.get("source_text")
                kw_loc = kw.get("location")
                if d_text == kw_text:
                    raise ContractError(
                        f"distractor source_text {d_text!r} overlaps with keyword source_text"
                    )
                if isinstance(kw_loc, dict) and _locations_overlap(d_loc, kw_loc):
                    raise ContractError(
                        f"distractor location overlaps with keyword location for source_text {d_text!r}"
                    )

    except ContractError as exc:
        errors.append(str(exc))

    # Validate distractors_report.json
    try:
        report = load_json_object(job_dir / "distractors_report.json")
        validate_question_id(report, question)

        if "candidate_count" not in report:
            raise ContractError("distractors_report.json missing 'candidate_count'")
        if not isinstance(report["candidate_count"], int):
            raise ContractError(
                f"candidate_count must be an int, got {type(report['candidate_count']).__name__}"
            )

        if "method" not in report:
            raise ContractError("distractors_report.json missing 'method'")
        if not isinstance(report["method"], str):
            raise ContractError(f"method must be a string, got {type(report['method']).__name__}")

        if "keyword_conflicts_excluded" not in report:
            raise ContractError("distractors_report.json missing 'keyword_conflicts_excluded'")
        kce = report["keyword_conflicts_excluded"]
        if not isinstance(kce, list) or not all(isinstance(x, str) for x in kce):
            raise ContractError("keyword_conflicts_excluded must be a list of strings")

        if "warnings" not in report:
            raise ContractError("distractors_report.json missing 'warnings'")
        if not isinstance(report["warnings"], list):
            raise ContractError("warnings must be a list")

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
