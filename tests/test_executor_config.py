import pytest
from pydantic import ValidationError

from server.app.executors import registration as _registration  # noqa: F401  # 触发内建 kind 注册
from server.app.executors.config import CodeExecutorConfig
from server.app.executors.definitions import load_executor_definitions
from server.app.executors.kinds import UnknownExecutorKindError


def test_loads_discriminated_executor_definitions() -> None:
    definitions = load_executor_definitions(
        {
            "code-default": {
                "kind": "code",
                "global_capacity": 4,
                "capabilities": {"fetch_questions": {"path": "workflow_nodes/question_intake.py"}},
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
            {"code-default": {"kind": "code", "global_capacity": capacity, "capabilities": {}}}
        )


def test_rejects_unknown_executor_kind() -> None:
    with pytest.raises(UnknownExecutorKindError, match="'bad'.*unknown kind 'unknown'"):
        load_executor_definitions(
            {"bad": {"kind": "unknown", "global_capacity": 1, "capabilities": {}}}
        )


def test_rejects_empty_capability_name() -> None:
    with pytest.raises(ValidationError):
        load_executor_definitions(
            {
                "code-default": {
                    "kind": "code",
                    "global_capacity": 4,
                    "capabilities": {"": {"path": "workflow_nodes/question_intake.py"}},
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


def test_loads_code_capability_config_schema() -> None:
    definitions = load_executor_definitions(
        {
            "code-default": {
                "kind": "code",
                "global_capacity": 4,
                "capabilities": {
                    "fetch_questions": {
                        "path": "workflow_nodes/question_intake.py",
                        "config_schema": {
                            "type": "object",
                            "properties": {"bank_version": {"type": "string"}},
                        },
                    }
                },
            }
        }
    )
    code = definitions["code-default"]
    assert isinstance(code, CodeExecutorConfig)
    assert code.capabilities["fetch_questions"].config_schema == {
        "type": "object",
        "properties": {"bank_version": {"type": "string"}},
    }


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "array"},
        {"type": "object", "properties": {"x": {"type": "weird"}}},
        {"type": "object", "properties": {"x": {"type": "integer", "default": "nan"}}},
    ],
)
def test_rejects_invalid_code_capability_config_schema(schema: object) -> None:
    with pytest.raises(ValidationError):
        load_executor_definitions(
            {
                "code-default": {
                    "kind": "code",
                    "global_capacity": 4,
                    "capabilities": {
                        "fetch_questions": {
                            "path": "workflow_nodes/question_intake.py",
                            "config_schema": schema,
                        }
                    },
                }
            }
        )
