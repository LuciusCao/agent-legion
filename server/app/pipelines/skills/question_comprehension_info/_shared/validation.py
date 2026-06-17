from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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


def validate_question_id(payload: dict[str, Any], question: dict[str, Any]) -> None:
    """Assert payload question_id matches the source question."""
    payload_id = payload.get("question_id")
    source_id = question.get("question_id")
    if payload_id != source_id:
        raise ContractError(f"question_id mismatch: payload={payload_id!r}, source={source_id!r}")


def load_valid_ability_ids(abilities_path: Path) -> set[str]:
    """Load the shared ability taxonomy and return all sub-ability IDs."""
    data = load_json_object(abilities_path)
    abilities = data.get("abilities", [])
    if not isinstance(abilities, list):
        raise ContractError("ability taxonomy 'abilities' must be a list")

    valid_ids: set[str] = set()
    for ability in abilities:
        if not isinstance(ability, dict):
            raise ContractError("ability taxonomy ability must be an object")
        sub_abilities = ability.get("sub_abilities", [])
        if not isinstance(sub_abilities, list):
            raise ContractError("ability taxonomy 'sub_abilities' must be a list")
        for sub in sub_abilities:
            if not isinstance(sub, dict):
                raise ContractError("ability taxonomy sub_ability must be an object")
            sub_id = sub.get("id")
            if not isinstance(sub_id, str):
                raise ContractError("ability taxonomy sub_ability id must be a string")
            valid_ids.add(sub_id)
    return valid_ids


def _validate_non_empty_string(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field_name} must be a non-empty string, got {value!r}")


def _validate_position(position: object) -> None:
    if not isinstance(position, dict):
        raise ContractError(f"content.position must be an object, got {type(position).__name__}")
    start = position.get("start")
    end = position.get("end")
    if not isinstance(start, int) or start < 0:
        raise ContractError(f"content.position.start must be a non-negative int, got {start!r}")
    if not isinstance(end, int) or end <= start:
        raise ContractError(f"content.position.end must be an int greater than start, got {end!r}")


def _validate_option(option: object, index: int) -> bool:
    if not isinstance(option, dict):
        raise ContractError(f"option at index {index} must be an object")
    _validate_non_empty_string(option.get("label"), f"option[{index}].label")
    _validate_non_empty_string(option.get("text"), f"option[{index}].text")
    is_correct = option.get("is_correct")
    if not isinstance(is_correct, bool):
        raise ContractError(f"option[{index}].is_correct must be a boolean, got {is_correct!r}")
    return is_correct


def validate_key_info_item(item: object, valid_ability_ids: set[str], index: int) -> None:
    if not isinstance(item, dict):
        raise ContractError(f"key_info item at index {index} must be an object")

    key_info_id = item.get("key_info_id")
    if not isinstance(key_info_id, str) or not key_info_id.startswith("ki_"):
        raise ContractError(f"key_info_id must start with 'ki_', got {key_info_id!r}")

    item_type = item.get("type")
    if item_type not in ("given", "hidden"):
        raise ContractError(f"type must be 'given' or 'hidden', got {item_type!r}")

    content = item.get("content")
    if not isinstance(content, dict):
        raise ContractError(f"content must be an object, got {type(content).__name__}")

    if item_type == "given":
        _validate_non_empty_string(content.get("text"), "content.text")
        _validate_position(content.get("position"))
    else:
        _validate_non_empty_string(content.get("derived_text"), "content.derived_text")
        _validate_position(content.get("position"))
        _validate_non_empty_string(content.get("derivation"), "content.derivation")

    question = item.get("question")
    if not isinstance(question, dict):
        raise ContractError(f"question must be an object, got {type(question).__name__}")
    _validate_non_empty_string(question.get("text"), "question.text")

    options = question.get("options")
    if not isinstance(options, list) or len(options) == 0:
        raise ContractError("question.options must be a non-empty array")

    has_correct = False
    for i, option in enumerate(options):
        if _validate_option(option, i):
            has_correct = True
    if not has_correct:
        raise ContractError("at least one question option must have is_correct == True")

    abilities = item.get("question_comprehension_abilities")
    if not isinstance(abilities, list) or len(abilities) == 0:
        raise ContractError("question_comprehension_abilities must be a non-empty array")
    for ability_id in abilities:
        if ability_id not in valid_ability_ids:
            raise ContractError(f"unknown question_comprehension_ability: {ability_id!r}")


def validate_key_info_payload(
    payload: dict[str, Any], question: dict[str, Any], valid_ability_ids: set[str]
) -> None:
    validate_question_id(payload, question)

    key_info_list = payload.get("key_info_list")
    if not isinstance(key_info_list, list) or len(key_info_list) == 0:
        raise ContractError("key_info_list must be a non-empty array")

    seen_ids: set[str] = set()
    for i, item in enumerate(key_info_list):
        validate_key_info_item(item, valid_ability_ids, i)
        item_id = item["key_info_id"]
        if item_id in seen_ids:
            raise ContractError(f"duplicate key_info_id: {item_id!r}")
        seen_ids.add(item_id)
