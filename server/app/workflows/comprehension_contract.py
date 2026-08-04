"""Field-whitelist contract guard for comprehension info schema v1.

Mirrors the upload contract (comprehension info schema v1, extra=forbid).
assemble must reject contract-violating items instead of leaking
review-process fields (e.g. `decision`/`reason`) into the package.
"""

from __future__ import annotations

from typing import Any

_KEY_INFO_ITEM_FIELDS = frozenset(
    {"key_info_id", "type", "content", "question", "question_comprehension_ability", "decision"}
)
_KEY_INFO_CONTENT_FIELDS = frozenset(
    {"text", "derived_text", "source_text", "position", "derivation", "fingerprint"}
)
_POSITION_FIELDS = frozenset({"start", "end"})
_SOCRATIC_QUESTION_FIELDS = frozenset({"text", "options"})
_SOCRATIC_OPTION_FIELDS = frozenset({"label", "text", "is_correct"})
_POSSIBLE_ERROR_ITEM_FIELDS = frozenset(
    {
        "error_id",
        "error_type",
        "position",
        "error_answer",
        "error_description",
        "cognitive_basis",
        "related_key_info_ids",
        "reason",
        "cognitive_basis_type",
    }
)


def _assert_no_extra_fields(path: str, value: Any, allowed: frozenset[str]) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    extra = sorted(set(value) - allowed)
    if extra:
        raise ValueError(f"{path} has fields outside the v1 contract: {', '.join(extra)}")


def assert_comprehension_lists_contract(key_info_list: Any, possible_error_list: Any) -> None:
    """Reject contract-violating key info / possible error items (schema v1)."""
    if not isinstance(key_info_list, list):
        raise ValueError("key_info_list must be a list")
    if not isinstance(possible_error_list, list):
        raise ValueError("possible_error_list must be a list")
    for index, item in enumerate(key_info_list):
        path = f"key_info_list[{index}]"
        _assert_no_extra_fields(path, item, _KEY_INFO_ITEM_FIELDS)
        if isinstance(item.get("content"), dict):
            content = item["content"]
            _assert_no_extra_fields(f"{path}.content", content, _KEY_INFO_CONTENT_FIELDS)
            if isinstance(content.get("position"), dict):
                _assert_no_extra_fields(
                    f"{path}.content.position", content["position"], _POSITION_FIELDS
                )
        if isinstance(item.get("question"), dict):
            question = item["question"]
            _assert_no_extra_fields(f"{path}.question", question, _SOCRATIC_QUESTION_FIELDS)
            options = question.get("options")
            if isinstance(options, list):
                for opt_index, option in enumerate(options):
                    _assert_no_extra_fields(
                        f"{path}.question.options[{opt_index}]", option, _SOCRATIC_OPTION_FIELDS
                    )
    for index, item in enumerate(possible_error_list):
        _assert_no_extra_fields(f"possible_error_list[{index}]", item, _POSSIBLE_ERROR_ITEM_FIELDS)
