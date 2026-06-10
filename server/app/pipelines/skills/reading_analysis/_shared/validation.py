from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ContractError(ValueError):
    """Raised when a skill output violates its deterministic contract."""


def load_json_object(path: Path) -> dict[str, Any]:
    """Load a JSON file and assert it is a single object."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ContractError(f"Expected JSON object in {path}, got {type(data).__name__}")
    return data


def load_single_question(path: Path) -> dict[str, Any]:
    """Load questions_parsed.json and return the single question dict."""
    payload = load_json_object(path)
    questions = payload.get("questions", [])
    if not isinstance(questions, list) or len(questions) != 1:
        raise ContractError(
            f"Expected exactly one question in {path}, found {len(questions) if isinstance(questions, list) else type(questions).__name__}"
        )
    question = questions[0]
    if not isinstance(question, dict):
        raise ContractError(f"Expected question dict in {path}, got {type(question).__name__}")
    return question


def question_options_by_key(question: dict[str, Any]) -> dict[str, str]:
    """Return a mapping of option key -> option text for the given question."""
    options: dict[str, str] = {}
    for opt in question.get("options", []):
        if isinstance(opt, dict):
            key = opt.get("key")
            text = opt.get("text")
            if isinstance(key, str) and isinstance(text, str):
                options[key] = text
    return options


def validate_question_id(payload: dict[str, Any], question: dict[str, Any]) -> None:
    """Assert payload question_id matches the source question."""
    payload_id = payload.get("question_id")
    source_id = question.get("question_id")
    if payload_id != source_id:
        raise ContractError(f"question_id mismatch: payload={payload_id!r}, source={source_id!r}")


def validate_source_location(
    question: dict[str, Any], source_text: str, location: dict[str, Any]
) -> None:
    """Validate a location points to an exact slice of stem or option text."""
    source = location.get("source")
    if source not in ("stem", "option"):
        raise ContractError(f"location.source must be stem or option, got {source!r}")

    if source == "stem":
        text = question.get("stem", "")
    else:
        options = question_options_by_key(question)
        option_key = location.get("option_key")
        if option_key is None:
            raise ContractError("location.option_key is required when source is 'option'")
        if option_key not in options:
            raise ContractError(f"location.option_key {option_key!r} not found in question options")
        text = options[option_key]

    start = location.get("start")
    end = location.get("end")

    if not isinstance(start, int) or start < 0:
        raise ContractError(f"location.start must be a non-negative int, got {start!r}")
    if not isinstance(end, int) or end <= start:
        raise ContractError(f"location.end must be an int greater than start, got {end!r}")

    if start > len(text) or end > len(text):
        raise ContractError(
            f"location range [{start}:{end}] out of range for text of length {len(text)}"
        )

    slice_text = text[start:end]
    if slice_text != source_text:
        raise ContractError(
            f"location source_text mismatch: expected {source_text!r}, got {slice_text!r} "
            f"at [{start}:{end}]"
        )


def validate_unique_ids(items: list[dict[str, Any]], prefix: str) -> None:
    """Assert all items have unique 'id' values."""
    seen: set[str] = set()
    for item in items:
        item_id = item.get("id")
        if not isinstance(item_id, str):
            raise ContractError(f"{prefix} item missing string 'id': {item!r}")
        if item_id in seen:
            raise ContractError(f"{prefix} duplicate id: {item_id!r}")
        seen.add(item_id)


def validate_confidence(value: Any) -> None:
    """Assert confidence is a number in [0, 1]."""
    if not isinstance(value, (int, float)):
        raise ContractError(f"confidence must be a number, got {type(value).__name__}")
    if not (0 <= value <= 1):
        raise ContractError(f"confidence must be in [0, 1], got {value!r}")


def validate_score_1_99(value: Any, field_name: str) -> None:
    """Assert value is an integer in [1, 99]."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ContractError(f"{field_name} must be an int, got {type(value).__name__}")
    if not (1 <= value <= 99):
        raise ContractError(f"{field_name} must be in [1, 99], got {value!r}")


def sha256_file(path: Path) -> str:
    """Return the hex SHA-256 digest of a file's contents."""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def validate_review_hash(source_path: Path, report: dict[str, Any]) -> None:
    """Assert report's source_artifact_sha256 matches the source file."""
    expected = sha256_file(source_path)
    actual = report.get("source_artifact_sha256")
    if actual != expected:
        raise ContractError(f"SHA-256 mismatch: expected {expected}, got {actual!r}")


def _normalize_json(data: Any) -> str:
    """Return a canonical JSON representation for semantic equality."""
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_exact_json_copy(source_path: Path, reviewed_path: Path) -> None:
    """Assert reviewed JSON is semantically identical to source JSON."""
    source = json.loads(source_path.read_text(encoding="utf-8"))
    reviewed = json.loads(reviewed_path.read_text(encoding="utf-8"))
    if _normalize_json(source) != _normalize_json(reviewed):
        raise ContractError(f"reviewed artifact must be an exact copy of {source_path.name}")
