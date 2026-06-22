import pytest
from pydantic import ValidationError

from server.app.executors.config import load_executor_definitions


def test_loads_discriminated_executor_definitions() -> None:
    definitions = load_executor_definitions(
        {
            "local-default": {
                "kind": "local",
                "global_capacity": 4,
                "capabilities": {
                    "fetch_questions": {"handler": "question_comprehension_info.fetch_questions"}
                },
            },
            "pi-default": {
                "kind": "pi",
                "global_capacity": 8,
                "capabilities": {
                    "review_keywords": {
                        "skill": "question_comprehension_info/review_key_info",
                        "tools": ["read", "write", "bash"],
                    }
                },
            },
            "openclaw-main": {
                "kind": "openclaw",
                "agent_id": "main",
                "global_capacity": 2,
                "capabilities": {"review_keywords": {"skill": "question-review-keywords"}},
            },
        }
    )
    assert definitions["pi-default"].kind == "pi"
    assert definitions["openclaw-main"].agent_id == "main"


@pytest.mark.parametrize("capacity", [0, -1, True])
def test_rejects_non_positive_global_capacity(capacity: object) -> None:
    with pytest.raises(ValidationError):
        load_executor_definitions(
            {"local-default": {"kind": "local", "global_capacity": capacity, "capabilities": {}}}
        )


def test_rejects_unknown_executor_kind() -> None:
    with pytest.raises(ValidationError):
        load_executor_definitions(
            {"bad": {"kind": "unknown", "global_capacity": 1, "capabilities": {}}}
        )


def test_rejects_empty_capability_name() -> None:
    with pytest.raises(ValidationError):
        load_executor_definitions(
            {
                "local-default": {
                    "kind": "local",
                    "global_capacity": 4,
                    "capabilities": {"": {"handler": "question_comprehension_info.fetch_questions"}},
                }
            }
        )


@pytest.mark.parametrize("skill", ["/absolute/path", "../escape", "foo/../bar"])
def test_rejects_unsafe_pi_skill_path(skill: str) -> None:
    with pytest.raises(ValidationError):
        load_executor_definitions(
            {
                "pi-default": {
                    "kind": "pi",
                    "global_capacity": 8,
                    "capabilities": {"review_keywords": {"skill": skill}},
                }
            }
        )
